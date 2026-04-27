from __future__ import annotations

import tempfile
import unittest
import os
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from daily_poster import __main__ as core


def make_settings(root: Path) -> core.Settings:
    return core.Settings(
        openai_api_key="test",
        openai_model="gpt-5.1",
        telegram_bot_token="token",
        telegram_chat_id="@channel",
        post_language="ru",
        post_max_chars=3500,
        post_temperature=0.8,
        activity_scan_roots=[root],
        activity_exclude_dirs=set(core.DEFAULT_EXCLUDE_DIRS),
        activity_max_files=80,
        activity_max_chars_per_file=6000,
        activity_max_total_chars=60000,
        spontaneous_enabled=True,
        spontaneous_min_pause_hours=6,
        autopilot_enabled=False,
        autopilot_posts_per_day=2,
        autopilot_min_pause_hours=4,
    )


class CoreBehaviorTests(unittest.TestCase):
    def test_checkpoint_filters_old_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state.json"
            snapshots = root / "snapshots"
            old_file = root / "old.txt"
            new_file = root / "new.txt"
            old_file.write_text("old\n", encoding="utf-8")
            old_ts = (datetime.now() - timedelta(minutes=10)).timestamp()
            os.utime(old_file, (old_ts, old_ts))

            with patch.object(core, "STATE_PATH", state), patch.object(core, "SNAPSHOTS_DIR", snapshots):
                core.write_checkpoint(datetime.now() - timedelta(seconds=1))
                new_file.write_text("new\n", encoding="utf-8")
                settings = make_settings(root)
                paths = {path.name for path in core.iter_changed_files(settings)}

            self.assertNotIn("old.txt", paths)
            self.assertIn("new.txt", paths)

    def test_binary_metadata_is_in_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state.json"
            snapshots = root / "snapshots"
            binary = root / "image.custombin"
            with patch.object(core, "STATE_PATH", state), patch.object(core, "SNAPSHOTS_DIR", snapshots):
                core.write_checkpoint(datetime.now() - timedelta(seconds=1))
                binary.write_bytes(b"\x00\x01\x02")
                settings = make_settings(root)
                context = core.build_activity_context(settings)

            self.assertIn("NAME: image.custombin", context)
            self.assertIn("KIND: binary", context)
            self.assertIn("SIZE_BYTES: 3", context)

    def test_details_do_not_refresh_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state.json"
            snapshots = root / "snapshots"
            text_file = root / "work.txt"
            text_file.write_text("one\n", encoding="utf-8")

            with patch.object(core, "STATE_PATH", state), patch.object(core, "SNAPSHOTS_DIR", snapshots):
                settings = make_settings(root)
                core.update_snapshot(text_file, "one")
                text_file.write_text("two\n", encoding="utf-8")
                first = core.changed_file_details(text_file, settings)
                second = core.changed_file_details(text_file, settings)

            self.assertIn("-one", first)
            self.assertIn("+two", first)
            self.assertIn("-one", second)
            self.assertIn("+two", second)

    def test_activity_digest_classifies_low_signal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state.json"
            snapshots = root / "snapshots"
            binary = root / "download.dat"
            with patch.object(core, "STATE_PATH", state), patch.object(core, "SNAPSHOTS_DIR", snapshots):
                core.write_checkpoint(datetime.now() - timedelta(seconds=1))
                binary.write_bytes(b"\x00\x01")
                settings = make_settings(root)
                stats = core.activity_stats(settings)

            self.assertEqual(stats["tone"], "low-signal")
            self.assertEqual(stats["weak_files"], 1)
            self.assertEqual(stats["strong_files"], 0)

    def test_sensitive_text_is_redacted_from_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state.json"
            snapshots = root / "snapshots"
            secret_file = root / "notes.txt"

            with patch.object(core, "STATE_PATH", state), patch.object(core, "SNAPSHOTS_DIR", snapshots):
                core.write_checkpoint(datetime.now() - timedelta(seconds=1))
                secret_file.write_text(
                    "OPENAI_API_KEY=sk-thisshouldberedacted1234567890\n"
                    "normal work note\n",
                    encoding="utf-8",
                )
                settings = make_settings(root)
                context = core.build_activity_context(settings)

            self.assertIn("[REDACTED]", context)
            self.assertNotIn("sk-thisshouldberedacted", context)

    def test_memory_updates_from_topic_post(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = Path(tmp) / "memory.json"
            with patch.object(core, "MEMORY_PATH", memory):
                core.update_memory(
                    "Трейты в Scala помогают собирать поведение маленькими частями.",
                    kind="topic",
                    topic="Трейты в Scala",
                )
                context = core.memory_context()

            self.assertIn("scala", context)
            self.assertIn("topic", context)
            self.assertIn("Трейты в Scala", context)

    def test_extract_response_text_from_nested_output(self) -> None:
        response = {
            "output": [
                {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": "Привет из nested output."}
                    ],
                }
            ]
        }

        self.assertEqual(core.extract_response_text(response), "Привет из nested output.")

    def test_extract_response_text_saves_debug_for_empty_response(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            debug_dir = Path(tmp) / "debug"
            with patch.object(core, "DEBUG_DIR", debug_dir):
                with self.assertRaises(core.ApiError) as raised:
                    core.extract_response_text(
                        {
                            "id": "resp_test",
                            "status": "incomplete",
                            "incomplete_details": {"reason": "max_output_tokens"},
                            "output": [],
                        }
                    )

            self.assertIn("Сырой ответ сохранен", str(raised.exception))
            self.assertTrue(any(debug_dir.glob("*resp_test.json")))


if __name__ == "__main__":
    unittest.main()
