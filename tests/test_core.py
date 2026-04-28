from __future__ import annotations

import tempfile
import unittest
import os
import io
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from daily_poster import __main__ as core


def make_settings(root: Path) -> core.Settings:
    return core.Settings(
        active_mode="tracking",
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
        spontaneous_min_pause_minutes=360,
        spontaneous_check_interval_minutes=60,
        autopilot_enabled=False,
        autopilot_posts_per_day=2,
        autopilot_min_pause_minutes=240,
        group_chat_enabled=False,
        group_chat_id="-1001",
        group_target_user_id="42",
        group_target_username="target",
        group_target_name="",
        group_reply_probability=0.12,
        group_min_pause_seconds=90,
        group_context_messages=16,
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

    def test_import_channel_history_from_telegram_json_and_skip_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            history = root / "posts.jsonl"
            memory = root / "memory.json"
            export = root / "export.json"
            export.write_text(
                """
                {
                  "messages": [
                    {"text": "Первый старый пост"},
                    {"text": [{"type": "plain", "text": "Второй "}, {"type": "plain", "text": "пост"}]},
                    {"text": "Первый старый пост"}
                  ]
                }
                """.strip(),
                encoding="utf-8",
            )

            with patch.object(core, "HISTORY_PATH", history), patch.object(core, "MEMORY_PATH", memory):
                imported, skipped = core.import_channel_history(export)
                recent = core.read_recent_posts(limit=10)
                context = core.memory_context()

            self.assertEqual(imported, 2)
            self.assertEqual(skipped, 1)
            self.assertIn("Первый старый пост", recent)
            self.assertIn("Профиль стиля базы канала", context)
            self.assertIn("второй пост", context)

    def test_import_channel_history_from_telegram_html_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            history = root / "posts.jsonl"
            memory = root / "memory.json"
            export_dir = root / "telegram-export"
            export_dir.mkdir()
            (export_dir / "messages.html").write_text(
                """
                <html><body>
                  <div class="message default clearfix">
                    <div class="text">Первый HTML пост</div>
                  </div>
                  <div class="message default clearfix">
                    <div class="text">Второй<br>HTML пост</div>
                  </div>
                </body></html>
                """.strip(),
                encoding="utf-8",
            )
            (export_dir / "messages2.html").write_text(
                """
                <html><body>
                  <div class="message default clearfix">
                    <div class="text">Третий HTML пост</div>
                  </div>
                </body></html>
                """.strip(),
                encoding="utf-8",
            )

            with patch.object(core, "HISTORY_PATH", history), patch.object(core, "MEMORY_PATH", memory):
                imported, skipped = core.import_channel_history(export_dir)
                recent = core.read_recent_posts(limit=10)

            self.assertEqual(imported, 3)
            self.assertEqual(skipped, 0)
            self.assertIn("Первый HTML пост", recent)
            self.assertIn("Второй\nHTML пост", recent)
            self.assertIn("Третий HTML пост", recent)

    def test_import_channel_history_replaces_previous_basis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            history = root / "posts.jsonl"
            memory = root / "memory.json"
            first = root / "first.json"
            second = root / "second.json"
            first.write_text('{"messages":[{"text":"Старый голос канала"}]}', encoding="utf-8")
            second.write_text('{"messages":[{"text":"Новая база канала"}]}', encoding="utf-8")

            with patch.object(core, "HISTORY_PATH", history), patch.object(core, "MEMORY_PATH", memory):
                first_imported, _ = core.import_channel_history(first)
                second_imported, _ = core.import_channel_history(second)
                recent = core.read_recent_posts(limit=10, preferred_kinds=("imported", "channel"))
                context = core.memory_context()

            self.assertEqual(first_imported, 1)
            self.assertEqual(second_imported, 1)
            self.assertNotIn("Старый голос канала", recent)
            self.assertIn("Новая база канала", recent)
            self.assertNotIn("старый голос канала", context.lower())
            self.assertIn("новая база канала", context.lower())

    def test_autogen_prompt_uses_style_profile_not_raw_imported_posts_as_examples(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            history = root / "posts.jsonl"
            memory = root / "memory.json"
            export = root / "export.json"
            export.write_text(
                '{"messages":[{"text":"Леон сделал тату. Я кинул ему ножик за три тыщи."}]}',
                encoding="utf-8",
            )

            with patch.object(core, "HISTORY_PATH", history), patch.object(core, "MEMORY_PATH", memory):
                core.import_channel_history(export)
                settings = make_settings(root)
                prompt = core.make_generation_input(
                    settings,
                    editorial_prompt="",
                    recent_posts=[],
                    activity_context="Свободный пост без файлового контекста.",
                    mode="autopilot",
                )

            self.assertIn("Профиль стиля базы канала", prompt)
            self.assertIn("Новый смысловой импульс", prompt)
            self.assertNotIn("Примеры недавних постов канала", prompt)
            self.assertNotIn("Леон сделал тату. Я кинул ему ножик за три тыщи.", prompt)

    def test_posts_stats_ignores_channel_basis_posts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            history = Path(tmp) / "posts.jsonl"
            today = core.date.today().isoformat()
            history.write_text(
                "\n".join(
                    [
                        f'{{"created_at":"{today}T10:00:00","kind":"imported","text":"архив"}}',
                        f'{{"created_at":"{today}T11:00:00","kind":"channel","text":"канал"}}',
                        f'{{"created_at":"{today}T12:00:00","kind":"autopilot","text":"живой пост"}}',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with patch.object(core, "HISTORY_PATH", history):
                stats = core.posts_stats()

            self.assertEqual(stats["today"], 1)
            self.assertEqual(stats["total"], 1)
            self.assertEqual(stats["by_kind"], {"autopilot": 1})

    def test_choose_maybe_post_mode_in_autogen_ignores_activity_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp))
            settings = core.Settings(
                **{**settings.__dict__, "active_mode": "autogen"}  # type: ignore[arg-type]
            )
            self.assertEqual(core.choose_maybe_post_mode(settings), "autopilot")

    def test_group_target_messages_build_style_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            group_memory = Path(tmp) / "group_memory.json"
            user = {"id": 42, "username": "target", "first_name": "Target"}

            with patch.object(core, "GROUP_MEMORY_PATH", group_memory):
                core.remember_group_message(user, "ну это прям сильно конечно!!", is_target=True)
                memory = core.read_group_memory()

            profile = memory.get("style_profile", {})
            self.assertEqual(profile.get("posts_analyzed"), 1)
            self.assertIn("сильно", profile.get("signature_words", []))

    def test_import_group_archive_json_lists_and_selects_participants(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            group_memory = root / "group_memory.json"
            export = root / "result.json"
            export.write_text(
                """
                {
                  "messages": [
                    {"from": "Артём", "from_id": "user1", "text": "ну это прям сильно конечно!!"},
                    {"from": "Наиль", "from_id": "user2", "text": "ахах понял"},
                    {"from": "Артём", "from_id": "user1", "text": [{"type": "plain", "text": "я бы так и "}, {"type": "plain", "text": "сказал"}]}
                  ]
                }
                """.strip(),
                encoding="utf-8",
            )

            with patch.object(core, "GROUP_MEMORY_PATH", group_memory):
                imported, participants = core.import_group_archive(export)
                selected = core.select_group_archive_participant("1")
                memory = core.read_group_memory()

            self.assertEqual(imported, 3)
            self.assertEqual(participants[0]["author_name"], "Артём")
            self.assertEqual(participants[0]["count"], 2)
            self.assertEqual(selected["author_id"], "user1")
            self.assertEqual(memory["style_profile"]["posts_analyzed"], 2)
            self.assertEqual(len(memory["target_messages"]), 2)

    def test_import_group_archive_html_extracts_authors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            group_memory = root / "group_memory.json"
            export_dir = root / "html"
            export_dir.mkdir()
            (export_dir / "messages.html").write_text(
                """
                <html><body>
                  <div class="message default clearfix">
                    <div class="from_name">Артём</div>
                    <div class="text">первое<br>сообщение</div>
                  </div>
                  <div class="message default clearfix">
                    <div class="from_name">Наиль</div>
                    <div class="text">второе сообщение</div>
                  </div>
                </body></html>
                """.strip(),
                encoding="utf-8",
            )

            with patch.object(core, "GROUP_MEMORY_PATH", group_memory):
                imported, participants = core.import_group_archive(export_dir)

            self.assertEqual(imported, 2)
            self.assertEqual({item["author_name"] for item in participants}, {"Артём", "Наиль"})

    def test_import_group_archive_html_uses_last_author_and_ignores_date_noise(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            group_memory = root / "group_memory.json"
            export = root / "messages.html"
            export.write_text(
                """
                <html><body>
                  <div class="message default clearfix">
                    <div class="from_name">
                      Alex Ere
                      <div class="date details" title="03.12.2025 10:44:42">10:44</div>
                    </div>
                    <div class="text">первое сообщение</div>
                  </div>
                  <div class="message default clearfix joined">
                    <div class="text">второе сообщение</div>
                  </div>
                  <div class="message default clearfix">
                    <div class="from_name">unknown</div>
                    <div class="text">это надо пропустить</div>
                  </div>
                </body></html>
                """.strip(),
                encoding="utf-8",
            )

            with patch.object(core, "GROUP_MEMORY_PATH", group_memory):
                imported, participants = core.import_group_archive(export)

            self.assertEqual(imported, 2)
            self.assertEqual(len(participants), 1)
            self.assertEqual(participants[0]["author_name"], "Alex Ere")
            self.assertEqual(participants[0]["count"], 2)

    def test_group_chat_polls_learns_and_replies_by_probability(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state.json"
            group_memory = root / "group_memory.json"
            settings = core.Settings(
                **{
                    **make_settings(root).__dict__,
                    "active_mode": "group",
                    "group_chat_enabled": True,
                    "group_chat_id": "-1001",
                    "group_target_user_id": "42",
                    "group_reply_probability": 1.0,
                    "group_min_pause_seconds": 0,
                }
            )
            updates = {
                "ok": True,
                "result": [
                    {
                        "update_id": 10,
                        "message": {
                            "message_id": 1,
                            "chat": {"id": -1001},
                            "from": {"id": 42, "username": "target", "first_name": "Target"},
                            "text": "да я бы так и сказал",
                        },
                    },
                    {
                        "update_id": 11,
                        "message": {
                            "message_id": 2,
                            "chat": {"id": -1001},
                            "from": {"id": 7, "username": "other", "first_name": "Other"},
                            "text": "ну что думаешь?",
                        },
                    },
                ],
            }

            sent_payloads: list[dict[str, object]] = []

            def fake_post_json(url: str, payload: dict[str, object], headers: dict[str, str] | None = None) -> dict[str, object]:
                if "api.openai.com" in url:
                    return {"output_text": "да норм звучит"}
                sent_payloads.append(payload)
                return {"ok": True, "result": {"message_id": 99}}

            with (
                patch.object(core, "STATE_PATH", state),
                patch.object(core, "GROUP_MEMORY_PATH", group_memory),
                patch.object(core, "get_settings", return_value=settings),
                patch.object(core, "get_json", return_value=updates),
                patch.object(core, "post_json", side_effect=fake_post_json),
            ):
                with redirect_stdout(io.StringIO()):
                    result = core.command_group_chat(argparse_namespace(force=False, reply_all=False))
                memory = core.read_group_memory()

            self.assertEqual(result, 0)
            self.assertEqual(len(sent_payloads), 1)
            self.assertEqual(sent_payloads[0]["chat_id"], -1001)
            self.assertEqual(sent_payloads[0]["reply_to_message_id"], 2)
            self.assertEqual(memory["style_profile"]["posts_analyzed"], 1)


def argparse_namespace(**kwargs: object) -> object:
    return type("Args", (), kwargs)()


if __name__ == "__main__":
    unittest.main()
