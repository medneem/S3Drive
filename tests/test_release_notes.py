"""Tests for English-only, incremental GitHub release notes."""
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.release_notes import (
    build_release_notes, generate, parse_git_log,
    section_for_subject, version_tuple,
)


class ReleaseNotesTests(unittest.TestCase):
    def test_release_body_is_english_delta_without_repeated_title(self):
        commits = [
            ("a" * 40, "ui: clarify S3 login/password labels and shorten Path-Style"),
            ("b" * 40, "fix: prevent S3 directory markers from reappearing"),
            ("c" * 40, "docs: explain Path-Style options"),
        ]
        notes = build_release_notes("v1.5.10", "v1.5.9", commits)
        self.assertTrue(notes.startswith("Changes since [v1.5.9]("))
        self.assertNotIn("# S3Drive v1.5.10", notes)
        self.assertIn("## Improvements\n\n- clarify S3 login/password", notes)
        self.assertIn("## Fixes\n\n- prevent S3 directory markers", notes)
        self.assertIn("## Documentation", notes)
        self.assertIn("aaaaaaa", notes)
        self.assertIn("compare/v1.5.9...v1.5.10", notes)
        self.assertNotIn("Package contents", notes)
        self.assertTrue(notes.isascii(), "Release description must be entirely in English/ASCII")

    def test_section_mapping_is_in_english(self):
        self.assertEqual(section_for_subject("feat: monitor S3 uploads"),
                         ("Features", "monitor S3 uploads"))
        self.assertEqual(section_for_subject("fix!: change config format"),
                         ("Breaking changes", "change config format"))
        self.assertEqual(section_for_subject("ui: rename credential fields"),
                         ("Improvements", "rename credential fields"))
        self.assertEqual(section_for_subject("Legacy change"),
                         ("Other changes", "Legacy change"))

    def test_non_english_subject_fails_instead_of_publishing_mixed_languages(self):
        with self.assertRaisesRegex(ValueError, "English"):
            build_release_notes(
                "v1.5.10", "v1.5.9",
                [("a" * 40, "fix: исправить название папки")],
            )

    def test_parses_git_log_and_rejects_malformed_records(self):
        raw = "a" * 40 + "\x1f" + "feat: add upload panel" + "\x1e\n"
        self.assertEqual(parse_git_log(raw),
                         [("a" * 40, "feat: add upload panel")])
        with self.assertRaises(ValueError):
            parse_git_log("bad-hash\x1fwrong\x1e")

    def test_no_changes_or_invalid_semver_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "No changes"):
            build_release_notes("v1.5.10", "v1.5.9", [])
        with self.assertRaises(ValueError):
            build_release_notes(
                "v1.5.10-preview", "v1.5.9",
                [("a" * 40, "feat: add something")],
            )
        self.assertEqual(version_tuple("v1.5.10"), (1, 5, 10))
        self.assertIsNone(version_tuple("1.5.10"))

    def test_generate_compares_to_explicit_latest_published_release(self):
        def fake_git(*args):
            if args[:2] == ("merge-base", "--is-ancestor"):
                self.assertEqual(args[2:], ("v1.5.9", "HEAD"))
                return ""
            self.assertEqual(args[:3], ("log", "--no-merges", "--format=%H%x1f%s%x1e"))
            self.assertEqual(args[-1], "v1.5.9..HEAD")
            return "b" * 40 + "\x1f" + "ui: clarify credential labels" + "\x1e\n"

        with patch("scripts.release_notes.git_output", side_effect=fake_git):
            notes = generate("v1.5.10", "v1.5.9")
        self.assertIn("clarify credential labels", notes)
        self.assertIn("Changes since [v1.5.9]", notes)

    def test_rejects_invalid_previous_release(self):
        with self.assertRaises(ValueError):
            generate("v1.5.10", "v1.5.10")
        with self.assertRaises(ValueError):
            generate("v1.5.10", "v1.6.0")
        with self.assertRaises(ValueError):
            generate("v1.5.10", "not-a-tag")

    def test_release_workflow_reads_latest_published_tag_and_generates_notes(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github" /
                    "workflows" / "build-windows.yml").read_text(encoding="utf-8")
        self.assertIn("fetch-depth: 0", workflow)
        self.assertIn("releases/latest", workflow)
        self.assertIn("--previous-tag", workflow)
        self.assertIn("python scripts/release_notes.py", workflow)
        self.assertIn('--notes-file "$notes"', workflow)
        self.assertNotIn('--generate-notes', workflow)


if __name__ == "__main__":
    unittest.main()
