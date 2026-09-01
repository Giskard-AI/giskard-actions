"""Write GitHub Actions step outputs without newline injection on scalars."""

import argparse
import json
import os
import secrets
import sys
from pathlib import Path


def sanitize_scalar(value: object) -> str:
    return str(value).replace("\r", "").replace("\n", "")


def multiline_delimiter(value: str) -> str:
    delimiter = "EOFMETA"
    while delimiter in value:
        delimiter = f"EOFMETA_{secrets.token_hex(8)}"
    return delimiter


def write_github_outputs(
    path: Path,
    *,
    scalars: dict[str, object] | None = None,
    multiline: dict[str, object] | None = None,
) -> None:
    with path.open("a", encoding="utf-8") as handle:
        for key, value in (scalars or {}).items():
            handle.write(f"{key}={sanitize_scalar(value)}\n")
        for key, value in (multiline or {}).items():
            text = "" if value is None else str(value)
            delimiter = multiline_delimiter(text)
            handle.write(f"{key}<<{delimiter}\n{text}\n{delimiter}\n")


def resolve_notes_output_path(output_path: str, version: str) -> str:
    cleaned_path = sanitize_scalar(output_path).strip()
    if cleaned_path:
        return cleaned_path
    cleaned_version = sanitize_scalar(version).strip()
    return f"release_notes/v{cleaned_version}.md"


def write_metadata_outputs(github_output: Path, metadata: dict) -> None:
    write_github_outputs(
        github_output,
        scalars={
            "notes_path": metadata.get("output_path") or "",
            "compare_url": metadata.get("compare_url") or "",
        },
        multiline={"summary": metadata.get("summary") or ""},
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--github-output", required=True)
    parser.add_argument("--metadata-file")
    parser.add_argument(
        "--resolve-output-path",
        action="store_true",
        help="Write sanitized composite output path from OUTPUT_PATH/VERSION env vars",
    )
    args = parser.parse_args(argv)
    github_output = Path(args.github_output)

    if args.resolve_output_path:
        write_github_outputs(
            github_output,
            scalars={
                "output": resolve_notes_output_path(
                    os.environ.get("INPUT_OUTPUT_PATH", ""),
                    os.environ.get("INPUT_VERSION", ""),
                )
            },
        )
        return 0

    if not args.metadata_file:
        print("error: --metadata-file is required unless --resolve-output-path", file=sys.stderr)
        return 2
    metadata = json.loads(Path(args.metadata_file).read_text(encoding="utf-8"))
    if not isinstance(metadata, dict):
        raise SystemExit("metadata JSON must be an object")
    write_metadata_outputs(github_output, metadata)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
