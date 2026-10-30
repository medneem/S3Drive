"""Описание релиза из Git-коммитов после предыдущего достижимого SemVer-тега."""
from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

VERSION_PATTERN = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
TYPE_PATTERN = re.compile(r"^([a-z]+)(?:\([^)]+\))?(!)?:\s*(.+)$", re.DOTALL)
SECTIONS = (
    "Несовместимые изменения",
    "Новые возможности",
    "Исправления",
    "Улучшения",
    "Документация",
    "Сборка и тестирование",
    "Другие изменения",
)


def version_tuple(tag: str) -> tuple[int, int, int] | None:
    match = VERSION_PATTERN.fullmatch(tag)
    return tuple(map(int, match.groups())) if match else None


def previous_release_tag(tags: list[str], new_tag: str) -> str | None:
    version = version_tuple(new_tag)
    if version is None:
        raise ValueError("Версия должна соответствовать формату vMAJOR.MINOR.PATCH.")
    candidates = [(parsed, tag) for tag in tags
                  if (parsed := version_tuple(tag)) is not None and parsed < version]
    return max(candidates)[1] if candidates else None


def parse_git_log(output: str) -> list[tuple[str, str]]:
    """Каждый коммит представлен как SHA, разделитель US, тема, разделитель RS."""
    commits = []
    for record in output.split("\x1e"):
        record = record.strip("\r\n")
        if not record:
            continue
        sha, separator, subject = record.partition("\x1f")
        if not separator or not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError("Получено некорректное сообщение коммита.")
        if subject.strip():
            commits.append((sha, subject.strip()))
    return commits


def section_for_subject(subject: str) -> tuple[str, str]:
    match = TYPE_PATTERN.match(subject)
    if not match:
        return "Другие изменения", subject
    commit_type, breaking, details = match.groups()
    if breaking:
        return "Несовместимые изменения", details
    category = {
        "feat": "Новые возможности",
        "fix": "Исправления",
        "perf": "Улучшения",
        "refactor": "Улучшения",
        "docs": "Документация",
        "test": "Сборка и тестирование",
        "ci": "Сборка и тестирование",
        "build": "Сборка и тестирование",
        "chore": "Другие изменения",
    }.get(commit_type, "Другие изменения")
    return category, details


def build_release_notes(new_tag: str, previous_tag: str | None,
                        commits: list[tuple[str, str]]) -> str:
    if version_tuple(new_tag) is None:
        raise ValueError("Некорректный номер версии.")
    if not commits:
        raise ValueError("С предыдущего релиза нет коммитов для описания изменений.")
    groups: dict[str, list[str]] = {section: [] for section in SECTIONS}
    for sha, subject in commits:
        section, description = section_for_subject(subject)
        # Только реальные сообщения коммитов, без выдумывания возможностей.
        groups[section].append(f"- {description} ({chr(96)}{sha[:7]}{chr(96)})")

    lines = [f"# S3Drive {new_tag}", "",
             f"Изменения с {previous_tag or 'начала истории проекта'}:", ""]
    for section in SECTIONS:
        if groups[section]:
            lines.extend([f"## {section}", "", *groups[section], ""])
    lines.extend([
        "## Состав пакета", "",
        "- Портативный архив Windows x64: S3Drive.exe, S3DriveAdmin.exe, "
        "rclone.exe и установщик WinFsp.", "",
        "> Не используйте S3-монтирование для баз данных и файлов, "
        "которые одновременно активно редактируют несколько пользователей.", "",
    ])
    return "\n".join(lines)


def git_output(*args: str) -> str:
    result = subprocess.run(["git", *args], check=True,
                            capture_output=True, text=True, encoding="utf-8")
    return result.stdout


def generate(new_tag: str) -> str:
    # --merged исключает чужие ветки, fetch-depth: 0 гарантирует историю.
    tags = git_output("tag", "--merged", "HEAD", "--list", "v*").splitlines()
    previous = previous_release_tag(tags, new_tag)
    revision_range = f"{previous}..HEAD" if previous else "HEAD"
    commits = parse_git_log(git_output(
        "log", "--no-merges", "--format=%H%x1f%s%x1e", revision_range))
    return build_release_notes(new_tag, previous, commits)


def main() -> int:
    parser = argparse.ArgumentParser(description="Описание релиза S3Drive по коммитам.")
    parser.add_argument("--version", required=True, help="Версия без префикса v")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    notes = generate(f"v{args.version}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(notes, encoding="utf-8")
    print(f"Создано описание релиза: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
