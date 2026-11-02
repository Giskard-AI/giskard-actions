"""CLI entrypoint for release notes generation."""

import argparse
import asyncio
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from .config import load_project_config
from .deps import dependency_updates_for_range, merge_dependency_notes
from .enrichment import fetch_github_issues_async, fetch_linear_issues_async
from .git_ops import (
    build_commit_digest,
    detect_tag_range,
    git_changed_files,
    git_diff_stat,
    git_file_diff,
    list_commits,
    run_git,
)
from .llm import DEFAULT_MODEL, consolidate_commit, synthesize_release_notes
from .models import (
    CommitCategory,
    CommitDigest,
    CommitInfo,
    ConsolidatedCommit,
    GitHubIssue,
    ReleaseNoteItem,
    ReleaseNotesPayload,
    payload_items,
    set_payload_section,
)
from .renderer import render_markdown
from .upgrade import apply_upgrade_rules, merge_upgrade_notes

LOG = logging.getLogger("release_notes")


def _setup_logging(log_level: str) -> None:
    """Configure root logging and silence noisy HTTP/SDK loggers."""
    level = getattr(logging, log_level.upper(), None)
    if level is None:
        raise SystemExit(f"Invalid --log-level: {log_level!r}")
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("giskard").setLevel(logging.WARNING)


def _compare_url(github_repository: str | None, tag_range) -> str | None:
    """Build the GitHub compare URL for the tag range, or None if no repo is known."""
    repo = github_repository or os.getenv("GITHUB_REPOSITORY")
    if not repo:
        return None
    return f"https://github.com/{repo}/compare/{tag_range.previous_tag}...{tag_range.current_tag}"


def parse_args() -> argparse.Namespace:
    """Define and parse CLI arguments."""
    p = argparse.ArgumentParser(
        description="Generate release notes from git history + LLM.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    p.add_argument(
        "--version",
        required=True,
        help="Version like 1.2.3 (current tag must be v<version>)",
    )
    p.add_argument("--previous-tag", default=None, help="Override previous tag (e.g., v1.2.2)")
    p.add_argument(
        "--output",
        required=True,
        help="Output markdown path",
    )
    p.add_argument(
        "--repo",
        default=".",
        help="Path to the git repository to analyze (default: current directory)",
    )
    p.add_argument(
        "--model",
        default=None,
        help=f"giskard-llm model name (default: RELEASE_NOTES_MODEL or {DEFAULT_MODEL})",
    )
    p.add_argument("--max-commit-digest-bytes", type=int, default=120_000)
    p.add_argument("--max-commit-file-diff-bytes", type=int, default=25_000)
    p.add_argument("--max-linear-issue-bytes", type=int, default=200_000)
    p.add_argument("--max-github-issue-bytes", type=int, default=200_000)
    p.add_argument("--llm-concurrency", type=int, default=10)
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip LLM and Linear calls; produce a deterministic debug output.",
    )
    p.add_argument(
        "--include-appendix",
        action="store_true",
        help="Include the full appendix (stats, commits, per-commit details).",
    )
    p.add_argument("--log-level", default="INFO")
    p.add_argument(
        "--env-file",
        default=None,
        help="Path to a .env file to load (e.g. /path/to/llm-hub/.env)",
    )
    p.add_argument(
        "--metadata-file",
        default=None,
        help="Write JSON metadata (tags, compare_url, summary, output_path).",
    )
    return p.parse_args()


async def main_async() -> None:
    """Main async pipeline: git → enrich (Linear/GitHub) → LLM → render → write.

    Steps:
    1. Parse args and load project config from the target repo.
    2. Detect tag range and list commits.
    3. Build commit digests in parallel (git subprocess I/O).
    4. Concurrently fetch Linear and GitHub issue enrichment.
    5. In non-dry-run mode, concurrently: consolidate each commit via LLM and
       compute dependency bumps across the full tag range.
    6. Synthesize the full ReleaseNotesPayload from consolidated commits.
    7. Render to markdown and write the output file + optional metadata JSON.
    """
    args = parse_args()
    _setup_logging(args.log_level)

    # Load .env: explicit --env-file first, then auto-detect from cwd
    if args.env_file:
        env_path = Path(args.env_file).resolve()
        if not env_path.exists():
            raise SystemExit(f"Env file not found: {env_path}")
        load_dotenv(env_path, override=False)
        LOG.info("Loaded env from %s", env_path)
    else:
        load_dotenv(override=False)  # auto-loads .env from cwd

    repo_root = Path(args.repo).resolve()
    if not (repo_root / ".git").exists():
        raise SystemExit(f"Not a git repository: {repo_root}")
    LOG.info("Repo root: %s", repo_root)

    config = load_project_config(repo_root)

    out_path = Path(args.output)
    if not out_path.is_absolute():
        out_path = Path.cwd() / out_path
    out_path = out_path.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    LOG.info("Output path: %s", out_path)

    tag_range = detect_tag_range(
        version=args.version,
        previous_tag_override=args.previous_tag,
        repo_root=repo_root,
        dry_run=bool(args.dry_run),
    )

    diff_stat = git_diff_stat(
        f"{tag_range.previous_tag}..{tag_range.current_tag}",
        repo_root=repo_root,
        ignored_basenames=set(config.ignored_basenames),
        ignored_path_prefixes=config.ignored_path_prefixes,
    )
    commit_list = run_git(
        [
            "log",
            "--no-merges",
            "--pretty=format:%h %ad %s",
            "--date=short",
            f"{tag_range.previous_tag}..{tag_range.current_tag}",
        ],
        repo_root,
    )

    model = args.model or os.getenv("RELEASE_NOTES_MODEL") or DEFAULT_MODEL

    commits = list_commits(tag_range=tag_range, repo_root=repo_root)
    LOG.info("Commits in range: %d", len(commits))

    # Task 2A: build commit digests in parallel (subprocess I/O per commit)
    _sem_git = asyncio.Semaphore(8)

    async def _build_one(c: CommitInfo) -> CommitDigest:
        async with _sem_git:
            return await asyncio.to_thread(
                build_commit_digest,
                c,
                repo_root,
                ignored_basenames=set(config.ignored_basenames),
                max_total_bytes=args.max_commit_digest_bytes,
                max_file_bytes=args.max_commit_file_diff_bytes,
                high_signal_prefixes=config.high_signal_prefixes,
            )

    commit_digests: list[CommitDigest] = list(
        await asyncio.gather(*(_build_one(c) for c in commits))
    )

    # Task 2B: run Linear + GitHub enrichment concurrently
    linear_api_key = os.getenv("LINEAR_API_KEY")
    github_token = os.getenv("GITHUB_TOKEN")
    github_repo = config.github_repository or os.getenv("GITHUB_REPOSITORY")

    async def _fetch_linear() -> dict:
        if args.dry_run or not linear_api_key:
            LOG.info(
                "Linear enrichment disabled (dry-run=%s, key=%s).",
                args.dry_run,
                bool(linear_api_key),
            )
            return {}
        all_linear_keys = [k for cd in commit_digests for k in cd.linear_keys]
        return await fetch_linear_issues_async(
            all_linear_keys,
            api_key=linear_api_key,
            max_issue_bytes=args.max_linear_issue_bytes,
        )

    async def _fetch_github() -> dict:
        if args.dry_run or not github_token or not github_repo:
            LOG.info(
                "GitHub enrichment disabled (dry-run=%s, token=%s, repo=%s).",
                args.dry_run,
                bool(github_token),
                github_repo,
            )
            return {}
        all_gh_numbers = [n for cd in commit_digests for n in cd.github_issue_numbers]
        all_gh_numbers += [cd.pr_number for cd in commit_digests if cd.pr_number]
        return await fetch_github_issues_async(
            all_gh_numbers,
            owner_repo=github_repo,
            token=github_token,
            max_issue_bytes=args.max_github_issue_bytes,
        )

    linear_issues_by_key, github_issues_by_number = await asyncio.gather(
        _fetch_linear(), _fetch_github()
    )
    github_issues_by_number: dict[int, GitHubIssue] = github_issues_by_number

    include_digests_in_spoilers = bool(args.dry_run)

    # Disable appendix unless explicitly requested
    if not args.include_appendix:
        config = config.with_section_disabled("appendix")

    consolidations: list[ConsolidatedCommit] = []
    if args.dry_run:
        consolidations = [
            ConsolidatedCommit(
                sha=cd.sha,
                subject=cd.subject,
                pr_number=cd.pr_number,
                linear_keys=cd.linear_keys,
                category=CommitCategory.OTHER,
                consolidated_description="DRY RUN: LLM skipped.",
                unknowns=["LLM/Linear skipped due to --dry-run."],
            )
            for cd in commit_digests
        ]
        payload = ReleaseNotesPayload(
            summary=[
                ReleaseNoteItem(
                    short_name="Dry run",
                    description=f"{len(commit_digests)} commits in range "
                    f"{tag_range.previous_tag}..{tag_range.current_tag}. "
                    "LLM generation was skipped.",
                ),
            ],
            unknowns=["LLM generation skipped due to --dry-run."],
        )
        range_dep_bumps = dependency_updates_for_range(
            tag_range, repo_root=repo_root, manifest_paths=config.manifest_paths
        )
    else:
        LOG.info("Model: %s", model)
        sem = asyncio.Semaphore(max(1, args.llm_concurrency))

        async def consolidate_one(cd: CommitDigest) -> ConsolidatedCommit:
            async with sem:
                issues = [
                    linear_issues_by_key[k] for k in cd.linear_keys if k in linear_issues_by_key
                ]
                gh_issues = [
                    github_issues_by_number[n]
                    for n in cd.github_issue_numbers
                    if n in github_issues_by_number
                ]
                # Also include the PR itself if fetched
                if cd.pr_number and cd.pr_number in github_issues_by_number:
                    pr_issue = github_issues_by_number[cd.pr_number]
                    if pr_issue not in gh_issues:
                        gh_issues.insert(0, pr_issue)
                return await consolidate_commit(
                    cd,
                    model=model,
                    linear_issues=issues,
                    github_issues=gh_issues,
                    repo_root=repo_root,
                    manifest_paths=config.manifest_paths,
                    config=config,
                )

        consolidations = list(await asyncio.gather(*(consolidate_one(cd) for cd in commit_digests)))
        # Task 2C: overlap dep-range computation with LLM synthesis
        payload, range_dep_bumps = await asyncio.gather(
            synthesize_release_notes(consolidations, model=model, config=config),
            asyncio.to_thread(
                dependency_updates_for_range,
                tag_range,
                repo_root=repo_root,
                manifest_paths=config.manifest_paths,
            ),
        )
    dep_bumps_with_notes = merge_dependency_notes(
        range_bumps=range_dep_bumps,
        consolidations=consolidations,
    )

    if config.upgrade_rules and "upgrade_notes" in config.enabled_section_ids():
        changed = git_changed_files(tag_range, repo_root=repo_root)
        deterministic = apply_upgrade_rules(config.upgrade_rules, changed)
        llm_notes = payload_items(payload, "upgrade_notes")
        set_payload_section(payload, "upgrade_notes", merge_upgrade_notes(deterministic, llm_notes))

    # Compute raw diffs for tracked files (e.g. helm values.yaml)
    file_diff_results: dict[str, str] = {
        path: git_file_diff(tag_range, path, repo_root=repo_root) for path in config.file_diffs
    }

    md = render_markdown(
        payload,
        tag_range,
        version=args.version,
        generated_at=datetime.now(tz=UTC),
        compare_url=_compare_url(config.github_repository, tag_range),
        diff_stat=diff_stat,
        commit_list=commit_list,
        commit_digests=commit_digests,
        consolidations=consolidations,
        linear_issues_by_key=linear_issues_by_key,
        github_issues_by_number=github_issues_by_number,
        include_digests_in_spoilers=include_digests_in_spoilers,
        dependency_bumps=dep_bumps_with_notes,
        file_diff_results=file_diff_results,
        config=config,
    )
    out_path.write_text(md, encoding="utf-8")
    LOG.info("Wrote %d bytes to %s", len(md.encode()), out_path)

    if args.metadata_file:
        compare = _compare_url(config.github_repository, tag_range)
        summary_lines = []
        for it in payload_items(payload, "summary"):
            name = it.short_name.strip()
            desc = it.description.strip()
            summary_lines.append(f"- {name}: {desc}" if name else f"- {desc}")
        metadata = {
            "previous_tag": tag_range.previous_tag,
            "current_tag": tag_range.current_tag,
            "compare_url": compare or "",
            "summary": "\n".join(summary_lines),
            "output_path": str(out_path),
        }
        meta_path = Path(args.metadata_file)
        if not meta_path.is_absolute():
            meta_path = Path.cwd() / meta_path
        meta_path = meta_path.resolve()
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        LOG.info("Wrote metadata to %s", meta_path)


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
