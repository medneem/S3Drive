"""Generate English release notes from commits since the last published release.

The release workflow supplies the previous tag from GitHub's releases/latest
endpoint. Never assume that the highest reachable git tag was published.
Do not publish untranslated commit subjects.
"""
from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

VERSION_PATTERN = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
TYPE_PATTERN = re.compile(r"^([a-z]+)(?:\([^)]+\))?(!)?:\s*(.+)$", re.DOTALL)
REPO_URL = "https://github.com/medneem/S3Drive"
SECTIONS = (
    "Breaking changes",
    "Features",
    "Fixes",
    "Improvements",
    "Documentation",
    "Build and tests",
    "Other changes",
)


def version_tuple(tag: str) -> tuple[int, int, int] | None:
    match = VERSION_PATTERN.fullmatch(tag)
    return tuple(map(int, match.groups())) if match else None


def parse_git_log(output: str) -> list[tuple[str, str]]:
    """Parse records as SHA, US, subject, RS (without losing newlines)."""
    commits = []
    for record in output.split("\x1e"):
        record = record.strip("\r\n")
        if not record:
            continue
        sha, separator, subject = record.partition("\x1f")
        if not separator or not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError("Invalid commit record from git log.")
        if subject.strip():
            commits.append((sha, subject.strip()))
    return commits


def section_for_subject(subject: str) -> tuple[str, str]:
    match = TYPE_PATTERN.match(subject)
    if not match:
        return "Other changes", subject
    commit_type, breaking, details = match.groups()
    if breaking:
        return "Breaking changes", details
    category = {
        "feat": "Features",
        "fix": "Fixes",
        "perf": "Improvements",
        "refactor": "Improvements",
        "ui": "Improvements",
        "docs": "Documentation",
        "test": "Build and tests",
        "ci": "Build and tests",
        "build": "Build and tests",
        "chore": "Other changes",
    }.get(commit_type, "Other changes")
    return category, details


def _validate_version_range(new_tag: str, previous_tag: str) -> None:
    current, previous = version_tuple(new_tag), version_tuple(previous_tag)
    if current is None or previous is None or previous >= current:
        raise ValueError(
            "Release tags must be valid ascending vMAJOR.MINOR.PATCH versions."
        )


def build_release_notes(new_tag: str, previous_tag: str,
                        commits: list[tuple[str, str]]) -> str:
    """Build a changelog body; GitHub already displays the release title."""
    _validate_version_range(new_tag, previous_tag)
    if not commits:
        raise ValueError(f"No changes since {previous_tag}; refusing an empty release.")

    groups: dict[str, list[str]] = {section: [] for section in SECTIONS}
    for sha, subject in commits:
        if not subject.isascii():
            raise ValueError(
                f"Commit {sha[:7]} has a non-ASCII subject; English release "
                "notes require an English commit summary before publishing."
            )
        section, description = section_for_subject(subject)
        groups[section].append(
            f"- {description} ([{sha[:7]}]({REPO_URL}/commit/{sha}))"
        )

    lines = [
        f"Changes since [{previous_tag}]({REPO_URL}/releases/tag/{previous_tag}):",
        "",
    ]
    for section in SECTIONS:
        if groups[section]:
            lines.extend([f"## {section}", "", *groups[section], ""])
    lines.extend([
        f"[Full changelog]({REPO_URL}/compare/{previous_tag}...{new_tag})",
        "",
    ])
    return "\n".join(lines)


def git_output(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], check=True, capture_output=True,
        text=True, encoding="utf-8",
    )
    return result.stdout


def generate(new_tag: str, previous_tag: str) -> str:
    _validate_version_range(new_tag, previous_tag)
    # The previous published release must lie on the actual release history.
    # Without this check, a rewritten branch could yield a misleading diff.
    try:
        git_output("merge-base", "--is-ancestor", previous_tag, "HEAD")
    except subprocess.CalledProcessError as exc:
        raise ValueError(
            f"The last published release {previous_tag} is not an ancestor "
            "of HEAD; refusing to generate misleading release notes."
        ) from exc
    commits = parse_git_log(git_output(
        "log", "--no-merges", "--format=%H%x1f%s%x1e",
        f"{previous_tag}..HEAD",
    ))
    return build_release_notes(new_tag, previous_tag, commits)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate English release notes since the latest published release."
    )
    parser.add_argument("--version", required=True, help="Version without the v prefix")
    parser.add_argument(
        "--previous-tag", required=True,
        help="Last published stable GitHub Release tag (e.g. v1.5.9)",
    )
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    notes = generate(f"v{args.version}", args.previous_tag)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(notes, encoding="utf-8")
    print(f"Generated English release notes since {args.previous_tag}: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
