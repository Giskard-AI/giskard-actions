"""Deterministic upgrade notes from git path changes + optional LLM extras."""

from pathlib import Path

from .config import UpgradeRule
from .models import ReleaseNoteItem


def _rule_matches(path: str, rule: UpgradeRule) -> bool:
    if path in rule.paths:
        return True
    name = Path(path).name.lower()
    for prefix in rule.path_prefixes:
        if not path.startswith(prefix):
            continue
        if not rule.filename_keywords:
            return True
        if any(k.lower() in name for k in rule.filename_keywords):
            return True
    return False


def apply_upgrade_rules(
    rules: tuple[UpgradeRule, ...], changed_files: list[str]
) -> list[ReleaseNoteItem]:
    """Return one upgrade note per matching rule, in config order."""
    notes: list[ReleaseNoteItem] = []
    for rule in rules:
        evidence = [p for p in changed_files if _rule_matches(p, rule)]
        if not evidence:
            continue
        notes.append(
            ReleaseNoteItem(
                short_name=rule.short_name,
                description=rule.description,
                evidence_paths=evidence[:10],
            )
        )
    return notes


def merge_upgrade_notes(
    deterministic: list[ReleaseNoteItem],
    llm_items: list[ReleaseNoteItem],
) -> list[ReleaseNoteItem]:
    """Keep all deterministic notes; append LLM notes whose short_name is new."""
    seen = {it.short_name.strip().lower() for it in deterministic if it.short_name.strip()}
    extras: list[ReleaseNoteItem] = []
    for it in llm_items:
        key = it.short_name.strip().lower()
        if key and key in seen:
            continue
        extras.append(it)
        if key:
            seen.add(key)
    return [*deterministic, *extras]
