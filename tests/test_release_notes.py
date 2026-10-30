"""Тесты формирования release notes на основе истории коммитов."""
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.release_notes import (
    build_release_notes, generate, parse_git_log,
    previous_release_tag, section_for_subject, version_tuple,
)


class ReleaseNotesTests(unittest.TestCase):
    def test_selects_nearest_lower_reachable_semver_tag(self):
        tags = ["v0.1.43", "v1.0.0", "v1.2.0", "v1.1.0-rc.1", "junk", "v1.0.1"]
        self.assertEqual(previous_release_tag(tags, "v1.1.0"), "v1.0.1")
        self.assertIsNone(previous_release_tag(["v1.1.0"], "v1.0.0"))
        self.assertEqual(version_tuple("v1.1.0"), (1, 1, 0))
        self.assertIsNone(version_tuple("1.1.0"))

    def test_groups_commit_messages_under_russian_headings(self):
        commits = [
            ("a" * 40, "feat: добавить мониторинг загрузок в S3"),
            ("b" * 40, "fix: исправить ярлык диска"),
            ("c" * 40, "docs: дополнить правила релизов"),
        ]
        notes = build_release_notes("v1.1.0", "v1.0.0", commits)
        self.assertIn("# S3Drive v1.1.0", notes)
        self.assertIn("Изменения с v1.0.0:", notes)
        self.assertIn("## Новые возможности\n\n- добавить мониторинг", notes)
        self.assertIn("## Исправления\n\n- исправить ярлык", notes)
        self.assertIn("## Документация", notes)
        self.assertIn("aaaaaaa", notes)

    def test_keeps_english_legacy_subjects_without_fabricated_translation(self):
        self.assertEqual(section_for_subject("feat: monitor S3 uploads"),
                         ("Новые возможности", "monitor S3 uploads"))
        self.assertEqual(section_for_subject("fix!: изменить формат"),
                         ("Несовместимые изменения", "изменить формат"))
        self.assertEqual(section_for_subject("Нестандартный коммит"),
                         ("Другие изменения", "Нестандартный коммит"))

    def test_parses_git_log_and_rejects_malformed_records(self):
        raw = "a" * 40 + "\x1f" + "feat: добавить панель" + "\x1e\n"
        self.assertEqual(parse_git_log(raw),
                         [("a" * 40, "feat: добавить панель")])
        with self.assertRaises(ValueError):
            parse_git_log("bad-hash\x1fwrong\x1e")

    def test_no_commits_or_invalid_version_does_not_create_generic_notes(self):
        with self.assertRaisesRegex(ValueError, "нет коммитов"):
            build_release_notes("v1.1.0", "v1.0.0", [])
        with self.assertRaises(ValueError):
            build_release_notes("v1.1.0-preview", "v1.0.0",
                                [("a" * 40, "feat: новый функционал")])

    def test_generate_uses_previous_tag_to_head(self):
        def fake_git(*args):
            if args[0] == "tag":
                return "v1.0.0\nv0.1.43\n"
            self.assertEqual(args[-1], "v1.0.0..HEAD")
            return ("b" * 40 + "\x1f" + "fix: исправить окно" + "\x1e\n")
        with patch("scripts.release_notes.git_output", side_effect=fake_git):
            notes = generate("v1.1.0")
        self.assertIn("исправить окно", notes)

    def test_workflow_uses_generated_notes_instead_of_static_description(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github" /
                    "workflows" / "build-windows.yml").read_text(encoding="utf-8")
        self.assertIn("fetch-depth: 0", workflow)
        self.assertIn("python scripts/release_notes.py", workflow)
        self.assertIn('--notes-file "$notes"', workflow)
        self.assertNotIn('--notes "Windows x64 release.', workflow)


if __name__ == "__main__":
    unittest.main()
