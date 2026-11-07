"""Tests for the bilingual UI and portable language preference."""
from __future__ import annotations

import ast
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from localization import ENGLISH, get_language, read_language, set_language, tr
from profiles import ProfileStore, new_profile


class LanguageTests(unittest.TestCase):
    def setUp(self):
        set_language("en")

    def tearDown(self):
        set_language("en")

    @staticmethod
    def store(path):
        return ProfileStore(
            path,
            encrypt=lambda value: "protected:" + value[::-1],
            decrypt=lambda value: value.removeprefix("protected:")[::-1],
        )

    def test_default_language_is_english_even_for_older_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text('{"profiles":[]}', encoding="utf-8")
            self.assertEqual(read_language(path), "en")
            store = self.store(path)
            self.assertEqual(store.load(), [])
            self.assertEqual(store.language, "en")

    def test_new_config_writes_explicit_english_language(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            store = self.store(path)
            self.assertEqual(store.load(), [])
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")),
                             {"language": "en", "profiles": []})

    def test_russian_language_persists_and_survives_profile_updates(self):
        profile = new_profile(
            name="Secure drive", endpoint="https://s3.example.com",
            bucket="backup", access_key="my-access", secret_key="my-secret",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            store = self.store(path)
            store.language = "ru"
            store.save([profile])
            raw = path.read_text(encoding="utf-8")
            self.assertNotIn("my-secret", raw)
            self.assertNotIn("my-access", raw)
            self.assertEqual(read_language(path), "ru")

            reopened = self.store(path)
            loaded = reopened.load()
            self.assertEqual(reopened.language, "ru")
            self.assertEqual(loaded, [profile])
            reopened.save(loaded)
            self.assertEqual(read_language(path), "ru")

    def test_invalid_language_cannot_overwrite_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            store = self.store(path)
            store.save([])
            original = path.read_bytes()
            store.language = "unknown"
            with self.assertRaisesRegex(ValueError, "language"):
                store.save([])
            self.assertEqual(path.read_bytes(), original)
            path.write_text('{"language":"unknown","profiles":[]}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "language"):
                self.store(path).load()
            self.assertEqual(read_language(path), "en")

    def test_translation_switches_and_supports_dynamic_statuses(self):
        self.assertEqual(tr("Новый"), "New")
        self.assertEqual(
            tr("Диск {drive} отключён.", drive="S:"),
            "Drive S: disconnected.",
        )
        set_language("ru")
        self.assertEqual(tr("Новый"), "Новый")
        self.assertEqual(
            tr("Диск {drive} отключён.", drive="S:"),
            "Диск S: отключён.",
        )
        with self.assertRaises(ValueError):
            set_language("de")
        self.assertEqual(get_language(), "ru")

    def test_all_explicit_russian_translation_keys_have_english_text(self):
        source = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "tr":
                if not node.args or not isinstance(node.args[0], ast.Constant):
                    continue
                key = node.args[0].value
                if isinstance(key, str) and any("\u0400" <= char <= "\u04ff" for char in key):
                    with self.subTest(key=key):
                        self.assertIn(key, ENGLISH)
                        self.assertNotEqual(ENGLISH[key], key)

    def test_main_window_has_settings_and_in_place_translation(self):
        import inspect
        from main import MainWindow, TransferWindow
        source = inspect.getsource(MainWindow)
        self.assertIn("self.settings_button.clicked.connect(self.open_settings)", source)
        self.assertIn("self.store.save(self.profiles)", inspect.getsource(MainWindow.open_settings))
        self.assertIn("self.retranslate_ui()", inspect.getsource(MainWindow.open_settings))
        self.assertIn("self.transfer_popup.retranslate_ui()", source)
        self.assertIn("self.table.setHorizontalHeaderLabels", inspect.getsource(TransferWindow.retranslate_ui))


if __name__ == "__main__":
    unittest.main()
