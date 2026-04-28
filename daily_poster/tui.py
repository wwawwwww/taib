from __future__ import annotations

import os
import plistlib
import shutil
import signal
import subprocess
import sys
import platform
import textwrap
from dataclasses import dataclass
from pathlib import Path

from daily_poster import __main__ as core

try:
    import curses
except ImportError as exc:  # pragma: no cover - depends on platform runtime.
    curses = None  # type: ignore[assignment]
    CURSES_IMPORT_ERROR = exc
else:
    CURSES_IMPORT_ERROR = None


LAUNCH_AGENT_PATH = Path.home() / "Library" / "LaunchAgents" / "com.codex.daily-poster.plist"
MAYBE_AGENT_PATH = Path.home() / "Library" / "LaunchAgents" / "com.codex.maybe-poster.plist"
AUTOPILOT_AGENT_PATH = Path.home() / "Library" / "LaunchAgents" / "com.codex.autopilot.plist"
IS_MACOS = platform.system() == "Darwin"
IS_WINDOWS = platform.system() == "Windows"
IS_LINUX = platform.system() == "Linux"


@dataclass(frozen=True)
class ModelOption:
    model: str
    label: str
    input_per_million: float
    output_per_million: float
    note: str

    def estimated_monthly(self, posts_per_month: int = 30) -> float:
        # Rough daily report estimate: scanned context + prompt + one Telegram post.
        input_tokens = 15000
        output_tokens = 1000
        per_post = (
            input_tokens * self.input_per_million / 1_000_000
            + output_tokens * self.output_per_million / 1_000_000
        )
        return per_post * posts_per_month


MODEL_OPTIONS = [
    ModelOption("gpt-5.2", "Best default", 1.75, 14.00, "Strongest general-purpose choice."),
    ModelOption("gpt-5.1", "Current configured family", 1.25, 10.00, "Good quality, a bit cheaper."),
    ModelOption("gpt-5-mini", "Budget", 0.25, 2.00, "Cheap and usually enough for daily reports."),
    ModelOption("gpt-5-nano", "Ultra budget", 0.05, 0.40, "Very cheap, weaker writing and inference."),
    ModelOption("gpt-5.2-pro", "Premium", 21.00, 168.00, "Very expensive; overkill for most reports."),
]


def run() -> None:
    if "--help" in sys.argv or "-h" in sys.argv:
        print("tgauto - TUI для Telegram-канала")
        print("")
        print("Запусти в интерактивном терминале:")
        print("  tgauto")
        return
    if curses is None:
        if IS_WINDOWS:
            print("Для TUI на Windows нужен пакет windows-curses.")
            print("Установи проект так: py -m pip install -e .")
        else:
            print(f"Не удалось импортировать curses: {CURSES_IMPORT_ERROR}")
        raise SystemExit(1)
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("tgauto нужно запускать в интерактивном терминале.")
        print("Попробуй: tgauto")
        raise SystemExit(1)
    curses.wrapper(TgAutoTui().main)


class TgAutoTui:
    def __init__(self) -> None:
        self.message = "Готово."
        self.selected = 0

    def main(self, stdscr: curses.window) -> None:
        set_cursor(0)
        stdscr.keypad(True)
        init_colors()

        actions = [
            (
                "Автогенерация",
                "Свободные посты в стилистике канала. Работает без привязки к локальным файлам.",
                self.autogen_hub,
            ),
            (
                "Отслеживание",
                "Посты по изменениям в выбранных папках и рабочему контексту с компьютера.",
                self.tracking_hub,
            ),
            (
                "Пост по теме",
                "Форсированный пост по твоей мысли, тезису или короткому промпту.",
                self.topic_post_menu,
            ),
            (
                "Групповой чат",
                "Ответы в группе с имитацией выбранного участника и настраиваемым шансом.",
                self.group_chat_hub,
            ),
            (
                "Настройки",
                "Канал, модель, prompt, расписание и технические параметры продукта.",
                self.settings_hub,
            ),
            (
                "Остановить бота",
                "Выключить режимы, снять фоновые агенты и завершить процессы публикации.",
                self.stop_bot_menu,
            ),
        ]

        while True:
            self.draw_menu(stdscr, actions)
            key = stdscr.getch()
            if key in (curses.KEY_UP, ord("k")):
                self.selected = (self.selected - 1) % len(actions)
            elif key in (curses.KEY_DOWN, ord("j")):
                self.selected = (self.selected + 1) % len(actions)
            elif key in (ord("\n"), curses.KEY_ENTER, 10, 13):
                _label, _subtitle, handler = actions[self.selected]
                if handler is None:
                    return
                handler(stdscr)
            elif key in (ord("q"), 27):
                return

    def draw_menu(self, stdscr: curses.window, actions: list[tuple[str, str, object]]) -> None:
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        title = "tgauto - авторский Telegram-бот"
        stdscr.addstr(1, 2, title[: w - 4], curses.color_pair(1) | curses.A_BOLD)
        stdscr.addstr(2, 2, "Основные действия: выбери раздел, Enter — открыть, q — выйти."[: w - 4])

        status = self.status_line()
        stdscr.addstr(4, 2, status[: w - 4], curses.color_pair(2))

        menu_height = max(1, (h - 10) // 3)
        top = max(0, min(self.selected - menu_height + 1, len(actions) - menu_height))
        row = 6
        for visible_idx, (label, subtitle, _handler) in enumerate(actions[top: top + menu_height]):
            idx = top + visible_idx
            y = row
            attr = curses.A_REVERSE if idx == self.selected else curses.A_NORMAL
            stdscr.addstr(y, 4, label[: w - 8], attr | curses.A_BOLD)
            for offset, line in enumerate(textwrap.wrap(subtitle, max(20, w - 10))[:2], start=1):
                stdscr.addstr(y + offset, 6, line[: w - 10], attr)
            row += 3

        self.draw_footer(stdscr)
        stdscr.refresh()

    def draw_footer(self, stdscr: curses.window) -> None:
        h, w = stdscr.getmaxyx()
        stdscr.hline(h - 3, 0, curses.ACS_HLINE, w)
        wrapped = textwrap.wrap(self.message, max(20, w - 4)) or [""]
        for idx, line in enumerate(wrapped[:2]):
            stdscr.addstr(h - 2 + idx, 2, line[: w - 4])

    def status_line(self) -> str:
        env = read_env()
        model = env.get("OPENAI_MODEL", "gpt-5.1")
        chat = env.get("TELEGRAM_CHAT_ID", "not set")
        mode = env.get("ACTIVE_MODE", "tracking")
        schedule_text = schedule_status()
        return f"Режим: {mode} | Модель: {model} | Канал: {chat} | Автозапуск: {schedule_text}"

    def autogen_hub(self, stdscr: curses.window) -> None:
        while True:
            env = read_env()
            lines = [
                "Автогенерация",
                "",
                "Этот режим ведет канал как самостоятельный автор:",
                "- опирается на память канала и его тон;",
                "- может публиковать свободные посты по ходу дня;",
                "- не использует локальные папки как основной источник.",
                "",
            ]
            lines.extend(self.mode_panel_lines("autogen"))
            lines.extend(
                [
                    "",
                    "1. Включить автогенерацию",
                    "2. Настроить частоту",
                    "3. База канала",
                    "4. Опубликовать сейчас",
                    "5. Назад",
                ]
            )
            self.show_option_screen(stdscr, lines)
            key = stdscr.getch()
            if key == ord("1"):
                self.activate_mode("autogen")
            elif key == ord("2"):
                self.configure_autogen(stdscr)
            elif key == ord("3"):
                self.import_history_menu(stdscr)
            elif key == ord("4"):
                answer = self.prompt(stdscr, "Запустить автопост сейчас? Напиши YES: ")
                if answer == "YES":
                    self.run_cli_and_show(stdscr, ["autopilot", "--force"])
                else:
                    self.message = "Автопост отменен."
            elif key in (ord("5"), ord("q"), 27):
                return

    def tracking_hub(self, stdscr: curses.window) -> None:
        while True:
            lines = [
                "Отслеживание",
                "",
                "Этот режим наблюдает за выбранными папками и собирает контекст по изменениям:",
                "- видит текстовые файлы, код и метаданные скачанных файлов;",
                "- пишет посты от первого лица без упоминания сканирования;",
                "- после публикации начинает отсчет заново с нового checkpoint.",
                "",
            ]
            lines.extend(self.mode_panel_lines("tracking"))
            lines.extend(
                [
                    "",
                    "1. Включить отслеживание",
                    "2. Выбрать папки для отслеживания",
                    "3. Сводка активности",
                    "4. Опубликовать пост сейчас",
                    "5. Назад",
                ]
            )
            self.show_option_screen(stdscr, lines)
            key = stdscr.getch()
            if key == ord("1"):
                self.activate_mode("tracking")
            elif key == ord("2"):
                self.configure_tracking_roots(stdscr)
            elif key == ord("3"):
                self.show_digest(stdscr)
            elif key == ord("4"):
                answer = self.prompt(stdscr, "Опубликовать пост по контексту сейчас? Напиши YES: ")
                if answer == "YES":
                    self.run_cli_and_show(stdscr, ["publish"])
                else:
                    self.message = "Публикация отменена."
            elif key in (ord("5"), ord("q"), 27):
                return

    def group_chat_hub(self, stdscr: curses.window) -> None:
        while True:
            env = read_env()
            enabled = env.get("ACTIVE_MODE") == "group" and env.get("GROUP_CHAT_ENABLED", "false") == "true"
            lines = [
                "Групповой чат",
                "",
                "Этот режим читает сообщения группы, учится на выбранном участнике и иногда отвечает в его манере.",
                "",
                f"Активен: {'да' if enabled else 'нет'}",
                f"Группа: {env.get('GROUP_CHAT_ID') or env.get('TELEGRAM_CHAT_ID', 'not set')}",
                f"Участник-образец: {env.get('GROUP_TARGET_USERNAME') or env.get('GROUP_TARGET_USER_ID') or env.get('GROUP_TARGET_NAME') or 'not set'}",
                f"Шанс ответа: {env.get('GROUP_REPLY_PROBABILITY', '0.12')}",
                f"Пауза между ответами: {env.get('GROUP_MIN_PAUSE_SECONDS', '90')} сек",
                "",
                "1. Включить режим группы",
                "2. Настроить группу и участника",
                "3. Проверить новые сообщения сейчас",
                "4. Загрузить архив и выбрать участника",
                "5. Назад",
            ]
            self.show_option_screen(stdscr, lines)
            key = stdscr.getch()
            if key == ord("1"):
                env["ACTIVE_MODE"] = "group"
                env["GROUP_CHAT_ENABLED"] = "true"
                write_env(env)
                sync_mode_agents("group")
                self.message = "Режим группового чата включен."
            elif key == ord("2"):
                self.configure_group_chat(stdscr)
            elif key == ord("3"):
                self.run_cli_and_show(stdscr, ["group-chat"])
            elif key == ord("4"):
                self.import_group_archive_menu(stdscr)
            elif key in (ord("5"), ord("q"), 27):
                return

    def settings_hub(self, stdscr: curses.window) -> None:
        while True:
            lines = [
                "Настройки",
                "",
                "Здесь собраны все параметры продукта без внутренних команд и служебного шума.",
                "",
                "1. Быстрая первичная настройка",
                "2. Канал и ключи",
                "3. Модель и стоимость",
                "4. Prompt автора",
                "5. Папки и лимиты отслеживания",
                "6. Диагностика",
                "0. Назад",
            ]
            self.show_option_screen(stdscr, lines)
            key = stdscr.getch()
            if key == ord("1"):
                self.setup_wizard(stdscr)
            elif key == ord("2"):
                self.configure_credentials(stdscr)
            elif key == ord("3"):
                self.choose_model(stdscr)
            elif key == ord("4"):
                self.edit_prompt(stdscr)
            elif key == ord("5"):
                self.configure_tracking_limits(stdscr)
            elif key == ord("6"):
                self.show_doctor(stdscr)
            elif key in (ord("0"), ord("q"), 27):
                return

    def stop_bot_menu(self, stdscr: curses.window) -> None:
        answer = self.prompt(stdscr, "Остановить все фоновые режимы и процессы бота? Напиши YES: ")
        if answer != "YES":
            self.message = "Остановка отменена."
            return
        killed = stop_all_bot_work()
        self.message = f"Бот остановлен: режимы выключены, процессов завершено: {killed}."

    def mode_panel_lines(self, mode: str) -> list[str]:
        env = read_env()
        configured = all(env.get(key) for key in ("OPENAI_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"))
        lines = [
            f"Настроено: {'да' if configured else 'нет'}",
            f"Активный режим продукта: {env.get('ACTIVE_MODE', 'tracking')}",
        ]
        try:
            settings = core.get_settings()
            lines.extend(core.mode_summary(settings, mode).splitlines())
        except Exception as exc:  # noqa: BLE001
            lines.append(f"Статус: нужна настройка ({exc})")
        return lines

    def activate_mode(self, mode: str) -> None:
        env = read_env()
        env["ACTIVE_MODE"] = mode
        if mode == "autogen":
            env.setdefault("AUTOPILOT_ENABLED", "true")
        write_env(env)
        sync_mode_agents(mode)
        self.message = f"Активный режим переключен на {mode}."

    def configure_autogen(self, stdscr: curses.window) -> None:
        env = read_env()
        env["ACTIVE_MODE"] = "autogen"
        env["AUTOPILOT_ENABLED"] = (
            self.prompt(stdscr, f"Включить автогенерацию true/false [{env.get('AUTOPILOT_ENABLED', 'true')}]: ")
            or env.get("AUTOPILOT_ENABLED", "true")
        )
        env["AUTOPILOT_POSTS_PER_DAY"] = (
            self.prompt(stdscr, f"Максимум постов в день [{env.get('AUTOPILOT_POSTS_PER_DAY', '2')}]: ")
            or env.get("AUTOPILOT_POSTS_PER_DAY", "2")
        )
        env["AUTOPILOT_MIN_PAUSE_MINUTES"] = (
            self.prompt(stdscr, f"Минимальная пауза между постами, минуты [{env.get('AUTOPILOT_MIN_PAUSE_MINUTES', '240')}]: ")
            or env.get("AUTOPILOT_MIN_PAUSE_MINUTES", "240")
        )
        env.pop("AUTOPILOT_MIN_PAUSE_HOURS", None)
        env["SPONTANEOUS_ENABLED"] = (
            self.prompt(stdscr, f"Разрешить спонтанные проверки true/false [{env.get('SPONTANEOUS_ENABLED', 'true')}]: ")
            or env.get("SPONTANEOUS_ENABLED", "true")
        )
        env["SPONTANEOUS_CHECK_INTERVAL_MINUTES"] = (
            self.prompt(stdscr, f"Как часто запускать проверку, минуты [{env.get('SPONTANEOUS_CHECK_INTERVAL_MINUTES', '60')}]: ")
            or env.get("SPONTANEOUS_CHECK_INTERVAL_MINUTES", "60")
        )
        env["SPONTANEOUS_MIN_PAUSE_MINUTES"] = (
            self.prompt(stdscr, f"Минимальная пауза для maybe-post, минуты [{env.get('SPONTANEOUS_MIN_PAUSE_MINUTES', '360')}]: ")
            or env.get("SPONTANEOUS_MIN_PAUSE_MINUTES", "360")
        )
        env.pop("SPONTANEOUS_MIN_PAUSE_HOURS", None)
        write_env(env)
        sync_mode_agents("autogen")
        self.message = "Настройки автогенерации обновлены."

    def configure_group_chat(self, stdscr: curses.window) -> None:
        env = read_env()
        env["ACTIVE_MODE"] = "group"
        env["GROUP_CHAT_ENABLED"] = (
            self.prompt(stdscr, f"Включить групповой чат true/false [{env.get('GROUP_CHAT_ENABLED', 'true')}]: ")
            or env.get("GROUP_CHAT_ENABLED", "true")
        )
        env["GROUP_CHAT_ID"] = (
            self.prompt(stdscr, f"ID или @username группы [{env.get('GROUP_CHAT_ID', '')}]: ")
            or env.get("GROUP_CHAT_ID", "")
        )
        env["GROUP_TARGET_USER_ID"] = (
            self.prompt(stdscr, f"ID участника-образца [{env.get('GROUP_TARGET_USER_ID', '')}]: ")
            or env.get("GROUP_TARGET_USER_ID", "")
        )
        env["GROUP_TARGET_USERNAME"] = (
            self.prompt(stdscr, f"@username участника-образца [{env.get('GROUP_TARGET_USERNAME', '')}]: ")
            or env.get("GROUP_TARGET_USERNAME", "")
        )
        env["GROUP_TARGET_NAME"] = (
            self.prompt(stdscr, f"Имя участника, если нет username/id [{env.get('GROUP_TARGET_NAME', '')}]: ")
            or env.get("GROUP_TARGET_NAME", "")
        )
        env["GROUP_REPLY_PROBABILITY"] = (
            self.prompt(stdscr, f"Шанс ответа 0..1 [{env.get('GROUP_REPLY_PROBABILITY', '0.12')}]: ")
            or env.get("GROUP_REPLY_PROBABILITY", "0.12")
        )
        env["GROUP_MIN_PAUSE_SECONDS"] = (
            self.prompt(stdscr, f"Минимальная пауза между ответами, сек [{env.get('GROUP_MIN_PAUSE_SECONDS', '90')}]: ")
            or env.get("GROUP_MIN_PAUSE_SECONDS", "90")
        )
        env["GROUP_CONTEXT_MESSAGES"] = (
            self.prompt(stdscr, f"Сколько последних сообщений учитывать [{env.get('GROUP_CONTEXT_MESSAGES', '16')}]: ")
            or env.get("GROUP_CONTEXT_MESSAGES", "16")
        )
        write_env(env)
        sync_mode_agents("group")
        self.message = "Групповой чат обновлен."

    def import_group_archive_menu(self, stdscr: curses.window) -> None:
        path = self.prompt(stdscr, "Путь к файлу или папке экспорта группового чата: ")
        if not path.strip():
            self.message = "Загрузка архива группы отменена."
            return
        try:
            imported, participants = core.import_group_archive(Path(path.strip()).expanduser().resolve(), replace=True)
        except Exception as exc:  # noqa: BLE001
            self.message = f"Не удалось загрузить архив группы: {exc}"
            return
        if not participants:
            self.message = "В архиве не найдено текстовых сообщений с участниками."
            return

        selected = self.choose_group_participant(stdscr, participants, imported)
        if selected is None:
            self.message = "Участник не выбран."
            return
        try:
            selected = core.select_group_archive_participant(str(selected.get("key") or selected.get("author_id") or selected.get("author_name")))
        except Exception as exc:  # noqa: BLE001
            self.message = f"Не удалось выбрать участника: {exc}"
            return

        env = read_env()
        env["ACTIVE_MODE"] = "group"
        env["GROUP_CHAT_ENABLED"] = env.get("GROUP_CHAT_ENABLED", "true")
        if selected.get("author_id"):
            env["GROUP_TARGET_USER_ID"] = str(selected.get("author_id"))
        env["GROUP_TARGET_NAME"] = str(selected.get("author_name", ""))
        write_env(env)
        self.message = f"Выбран стиль участника: {selected.get('author_name', 'unknown')}."

    def choose_group_participant(
        self,
        stdscr: curses.window,
        participants: list[dict[str, object]],
        imported: int,
    ) -> dict[str, object] | None:
        selected = 0
        offset = 0
        while True:
            stdscr.erase()
            h, w = stdscr.getmaxyx()
            stdscr.addstr(1, 2, f"Загружено сообщений: {imported}"[: w - 4], curses.color_pair(1) | curses.A_BOLD)
            stdscr.addstr(2, 2, "Выбери участника стрелками, Enter — выбрать, q — назад."[: w - 4])
            view_height = max(1, h - 6)
            if selected < offset:
                offset = selected
            elif selected >= offset + view_height:
                offset = selected - view_height + 1

            for row, participant in enumerate(participants[offset: offset + view_height], start=0):
                idx = offset + row
                attr = curses.A_REVERSE if idx == selected else curses.A_NORMAL
                name = str(participant.get("author_name", "unknown"))
                author_id = str(participant.get("author_id") or participant.get("key") or "")
                count = participant.get("count", 0)
                line = f"{idx + 1}. {name} | {author_id} | {count} сообщений"
                stdscr.addstr(4 + row, 2, line[: w - 4], attr)

            self.draw_footer(stdscr)
            stdscr.refresh()
            key = stdscr.getch()
            if key in (ord("q"), 27):
                return None
            if key in (curses.KEY_UP, ord("k")):
                selected = (selected - 1) % len(participants)
            elif key in (curses.KEY_DOWN, ord("j")):
                selected = (selected + 1) % len(participants)
            elif key == curses.KEY_NPAGE:
                selected = min(len(participants) - 1, selected + view_height)
            elif key == curses.KEY_PPAGE:
                selected = max(0, selected - view_height)
            elif key in (10, 13, curses.KEY_ENTER):
                return participants[selected]

    def configure_tracking_roots(self, stdscr: curses.window) -> None:
        env = read_env()
        current = env.get("ACTIVITY_SCAN_ROOTS", core.DEFAULT_SCAN_ROOTS)
        value = self.prompt(stdscr, f"Папки через запятую [{current}]: ")
        if value.strip():
            env["ACTIVITY_SCAN_ROOTS"] = value.strip()
        env["ACTIVE_MODE"] = "tracking"
        write_env(env)
        sync_mode_agents("tracking")
        self.message = "Папки для отслеживания обновлены."

    def configure_credentials(self, stdscr: curses.window) -> None:
        env = read_env()
        fields = [
            ("OPENAI_API_KEY", "OpenAI API key", True),
            ("TELEGRAM_BOT_TOKEN", "Telegram bot token", True),
            ("TELEGRAM_CHAT_ID", "Канал (@username или -100...)", False),
        ]
        for key, label, hidden in fields:
            current = env.get(key, "")
            shown = mask(current) if hidden else current
            value = self.prompt(stdscr, f"{label} [{shown}]: ", hidden=hidden)
            if value.strip():
                env[key] = value.strip()
        write_env(env)
        self.message = "Канал и ключи обновлены."

    def configure_tracking_limits(self, stdscr: curses.window) -> None:
        env = read_env()
        mapping = [
            ("ACTIVITY_SCAN_ROOTS", "Папки для отслеживания"),
            ("ACTIVITY_MAX_FILES", "Максимум файлов за проход"),
            ("ACTIVITY_MAX_CHARS_PER_FILE", "Лимит символов на файл"),
            ("ACTIVITY_MAX_TOTAL_CHARS", "Лимит символов всего"),
        ]
        for key, label in mapping:
            current = env.get(key, "")
            value = self.prompt(stdscr, f"{label} [{current}]: ")
            if value.strip():
                env[key] = value.strip()
        write_env(env)
        self.message = "Параметры отслеживания обновлены."

    def import_history_menu(self, stdscr: curses.window) -> None:
        path = self.prompt(stdscr, "Путь к файлу или папке экспорта Telegram для базы канала: ")
        if not path.strip():
            self.message = "Загрузка базы канала отменена."
            return
        confirm = self.prompt(stdscr, "Заменить текущую базу канала этой историей? Напиши YES: ")
        if confirm != "YES":
            self.message = "Загрузка базы канала отменена."
            return
        self.run_cli_and_show(stdscr, ["import-history", path.strip()])

    def show_option_screen(self, stdscr: curses.window, lines: list[str]) -> None:
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        for idx, line in enumerate(lines[: h - 3]):
            attr = curses.color_pair(1) | curses.A_BOLD if idx == 0 else curses.A_NORMAL
            stdscr.addstr(idx + 1, 2, line[: w - 4], attr)
        self.draw_footer(stdscr)
        stdscr.refresh()

    def setup_wizard(self, stdscr: curses.window) -> None:
        env = read_env()
        steps = [
            "Мастер настройки проведет по основным шагам.",
            "Можно нажимать Enter, чтобы оставить текущее значение.",
            "Секреты не показываются полностью.",
            "",
        ]
        self.show_text(stdscr, steps)

        fields = [
            ("OPENAI_API_KEY", "OpenAI API key", True),
            ("TELEGRAM_BOT_TOKEN", "Telegram bot token из BotFather", True),
            ("TELEGRAM_CHAT_ID", "Канал для публикации (@username или -100...)", False),
            ("OPENAI_MODEL", "Модель OpenAI", False),
            ("ACTIVE_MODE", "Режим продукта: tracking или autogen", False),
            ("ACTIVITY_SCAN_ROOTS", "Папки для отслеживания через запятую", False),
            ("SPONTANEOUS_CHECK_INTERVAL_MINUTES", "Как часто запускать проверку, минуты", False),
            ("SPONTANEOUS_MIN_PAUSE_MINUTES", "Минимальная пауза между любыми постами, минуты", False),
            ("AUTOPILOT_ENABLED", "Автоведение канала включено? true/false", False),
            ("AUTOPILOT_POSTS_PER_DAY", "Сколько автопостов максимум в день", False),
        ]

        defaults = {
            "OPENAI_MODEL": "gpt-5-mini",
            "ACTIVE_MODE": "tracking",
            "ACTIVITY_SCAN_ROOTS": core.DEFAULT_SCAN_ROOTS,
            "SPONTANEOUS_CHECK_INTERVAL_MINUTES": "60",
            "SPONTANEOUS_MIN_PAUSE_MINUTES": "360",
            "AUTOPILOT_ENABLED": "false",
            "AUTOPILOT_POSTS_PER_DAY": "2",
        }
        for key, label, secret in fields:
            current = env.get(key, defaults.get(key, ""))
            shown = mask(current) if secret else current
            value = self.prompt(stdscr, f"{label} [{shown}]: ", hidden=secret)
            if value.strip():
                env[key] = value.strip()
            elif key not in env and current:
                env[key] = current

        write_env(env)

        if IS_MACOS:
            schedule = self.prompt(stdscr, "Настроить ежедневный пост на 21:00? YES/[Enter]: ")
            if schedule == "YES":
                update_plist_schedule(core.PROJECT_ROOT / "automation" / "com.codex.daily-poster.plist", 21, 0)
                install_launch_agent(core.PROJECT_ROOT / "automation" / "com.codex.daily-poster.plist", LAUNCH_AGENT_PATH)

            maybe = self.prompt(stdscr, "Включить фоновые maybe-post проверки раз в час? YES/[Enter]: ")
            if maybe == "YES":
                install_launch_agent(core.PROJECT_ROOT / "automation" / "com.codex.maybe-poster.plist", MAYBE_AGENT_PATH)

            autopilot = self.prompt(stdscr, "Включить автоведение канала? YES/[Enter]: ")
            if autopilot == "YES":
                env = read_env()
                env["AUTOPILOT_ENABLED"] = "true"
                write_env(env)
                source = core.PROJECT_ROOT / "automation" / "com.codex.autopilot.plist"
                if source.exists():
                    install_launch_agent(source, AUTOPILOT_AGENT_PATH)
        else:
            self.message = f"Мастер завершен. Автозапуск для {platform.system()} настраивается по README."

        self.message = "Мастер настройки завершен."

    def edit_prompt(self, stdscr: curses.window) -> None:
        while True:
            stdscr.erase()
            h, w = stdscr.getmaxyx()
            stdscr.addstr(1, 2, "Редактировать prompt"[: w - 4], curses.color_pair(1) | curses.A_BOLD)
            stdscr.addstr(3, 2, f"Файл: {core.PROMPT_PATH}"[: w - 4])
            stdscr.addstr(5, 4, "1. Посмотреть текущий prompt"[: w - 8])
            stdscr.addstr(6, 4, "2. Заменить prompt из терминала"[: w - 8])
            stdscr.addstr(7, 4, "3. Открыть в $EDITOR"[: w - 8])
            stdscr.addstr(8, 4, "4. Назад"[: w - 8])
            self.draw_footer(stdscr)
            key = stdscr.getch()
            if key == ord("1"):
                text = core.PROMPT_PATH.read_text(encoding="utf-8") if core.PROMPT_PATH.exists() else ""
                self.show_text(stdscr, text.splitlines() or ["Prompt пустой."])
            elif key == ord("2"):
                self.replace_prompt_from_terminal(stdscr)
            elif key == ord("3"):
                self.open_prompt_in_editor(stdscr)
            elif key in (ord("4"), ord("q"), 27):
                return

    def replace_prompt_from_terminal(self, stdscr: curses.window) -> None:
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        lines = [
            "Вставь новый prompt. Можно писать по-русски.",
            "Заверши строкой, где будет только точка: .",
            "Пустая первая строка отменит изменение.",
            "",
        ]
        for idx, line in enumerate(lines):
            stdscr.addstr(1 + idx, 2, line[: w - 4])
        stdscr.refresh()

        curses.echo(True)
        set_cursor(1)
        collected: list[str] = []
        y = 1 + len(lines)
        while y < h - 2:
            stdscr.move(y, 2)
            stdscr.clrtoeol()
            raw = stdscr.getstr(y, 2, max(1, w - 4)).decode("utf-8", errors="replace")
            if raw == ".":
                break
            if not raw and not collected:
                set_cursor(0)
                curses.echo(False)
                self.message = "Редактирование prompt отменено."
                return
            collected.append(raw)
            y += 1

        set_cursor(0)
        curses.echo(False)
        new_prompt = "\n".join(collected).strip()
        if not new_prompt:
            self.message = "Prompt не изменен: пустой ввод."
            return
        backup = backup_prompt()
        core.PROMPT_PATH.write_text(new_prompt + "\n", encoding="utf-8")
        self.message = f"Prompt обновлен. Backup: {backup.name}"

    def open_prompt_in_editor(self, stdscr: curses.window) -> None:
        editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "nano"
        backup = backup_prompt()
        curses.endwin()
        try:
            subprocess.run([editor, str(core.PROMPT_PATH)], check=False)
            self.message = f"Редактор prompt закрыт. Backup: {backup.name}"
        except OSError as exc:
            self.message = f"Не удалось открыть редактор `{editor}`: {exc}"
        finally:
            stdscr.refresh()

    def choose_model(self, stdscr: curses.window) -> None:
        current = read_env().get("OPENAI_MODEL", "gpt-5.1")
        idx = next((i for i, option in enumerate(MODEL_OPTIONS) if option.model == current), 0)

        while True:
            stdscr.erase()
            h, w = stdscr.getmaxyx()
            stdscr.addstr(1, 2, "Choose model"[: w - 4], curses.color_pair(1) | curses.A_BOLD)
            stdscr.addstr(2, 2, "Estimated monthly cost assumes 30 posts, ~15k input tokens and 1k output tokens per post."[: w - 4])

            for row, option in enumerate(MODEL_OPTIONS, start=4):
                selected = row - 4 == idx
                attr = curses.A_REVERSE if selected else curses.A_NORMAL
                price = f"${option.input_per_million:g}/M in, ${option.output_per_million:g}/M out"
                monthly = f"~${option.estimated_monthly():.2f}/month"
                line = f"{option.model:<15} {option.label:<24} {price:<28} {monthly:<12} {option.note}"
                stdscr.addstr(row, 2, line[: w - 4], attr)

            self.draw_footer(stdscr)
            key = stdscr.getch()
            if key in (curses.KEY_UP, ord("k")):
                idx = (idx - 1) % len(MODEL_OPTIONS)
            elif key in (curses.KEY_DOWN, ord("j")):
                idx = (idx + 1) % len(MODEL_OPTIONS)
            elif key in (10, 13, curses.KEY_ENTER):
                env = read_env()
                env["OPENAI_MODEL"] = MODEL_OPTIONS[idx].model
                write_env(env)
                self.message = f"Model set to {MODEL_OPTIONS[idx].model}."
                return
            elif key in (ord("q"), 27):
                return

    def configure_schedule(self, stdscr: curses.window) -> None:
        if not IS_MACOS:
            lines = [
                "Автозапуск через TUI сейчас настраивается только на macOS.",
                "",
                "Для этой системы используй пошаговую инструкцию из README:",
                "- Linux: cron или systemd --user",
                "- Windows: Task Scheduler",
                "",
                f"Текущая система: {platform.system()}",
            ]
            self.show_text(stdscr, lines)
            return
        current = read_schedule() or (21, 0)
        hour_text = self.prompt(stdscr, f"Hour 0-23 [{current[0]}]: ")
        minute_text = self.prompt(stdscr, f"Minute 0-59 [{current[1]}]: ")
        try:
            hour = int(hour_text) if hour_text.strip() else current[0]
            minute = int(minute_text) if minute_text.strip() else current[1]
        except ValueError:
            self.message = "Schedule was not changed: hour and minute must be numbers."
            return
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            self.message = "Schedule was not changed: invalid time."
            return

        update_plist_schedule(core.PROJECT_ROOT / "automation" / "com.codex.daily-poster.plist", hour, minute)
        LAUNCH_AGENT_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(core.PROJECT_ROOT / "automation" / "com.codex.daily-poster.plist", LAUNCH_AGENT_PATH)
        reload_launch_agent()
        self.message = f"Daily schedule set to {hour:02d}:{minute:02d}."

    def show_digest(self, stdscr: curses.window) -> None:
        try:
            settings = core.get_settings()
            text = core.activity_digest(settings)
        except Exception as exc:  # noqa: BLE001
            text = f"Error: {exc}"
        self.show_text(stdscr, text.splitlines())

    def mark_checkpoint_now(self, stdscr: curses.window) -> None:
        answer = self.prompt(stdscr, "Mark all current files as accounted for? Type YES: ")
        if answer != "YES":
            self.message = "Checkpoint update cancelled."
            return
        try:
            settings = core.get_settings()
            core.write_checkpoint()
            count = core.refresh_snapshots(settings)
            self.message = f"Checkpoint updated. Text snapshots refreshed: {count}."
        except Exception as exc:  # noqa: BLE001
            self.message = f"Checkpoint update failed: {exc}"

    def show_doctor(self, stdscr: curses.window) -> None:
        try:
            settings = core.get_settings()
            lines = [
                "tgauto doctor",
                "",
                f"Project: {core.PROJECT_ROOT}",
                f"Model: {settings.openai_model}",
                f"Channel: {settings.telegram_chat_id}",
                f"Checkpoint: {core.checkpoint_label()}",
                "",
                core.activity_digest(settings),
            ]
            if IS_MACOS:
                launch_agent = LAUNCH_AGENT_PATH
                if not launch_agent.exists():
                    lines.extend(["", f"Warning: LaunchAgent is not installed: {launch_agent}"])
            else:
                lines.extend(["", f"Автозапуск для {platform.system()} настраивается по README."])
            if any(path == Path.home() for path in settings.activity_scan_roots):
                lines.extend(["", "Warning: scanning the whole home folder can be noisy and expensive."])
        except Exception as exc:  # noqa: BLE001
            lines = [f"Error: {exc}"]
        self.show_text(stdscr, "\n".join(lines).splitlines())

    def view_logs(self, stdscr: curses.window) -> None:
        log_path = core.LOGS_DIR / "daily.log"
        if not log_path.exists():
            self.show_text(stdscr, [f"No log file yet: {log_path}"])
            return
        try:
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            lines = [f"Could not read logs: {exc}"]
        self.show_text(stdscr, lines[-300:] or ["Log file is empty."])

    def changed_files_menu(self, stdscr: curses.window) -> None:
        selected = 0
        summaries: list[dict[str, str | int]] = []

        def refresh() -> None:
            nonlocal summaries, selected
            settings = core.get_settings()
            summaries = core.changed_file_summaries(settings)
            selected = min(selected, max(0, len(summaries) - 1))

        try:
            refresh()
        except Exception as exc:  # noqa: BLE001
            self.show_text(stdscr, [f"Error: {exc}"])
            return

        while True:
            stdscr.erase()
            h, w = stdscr.getmaxyx()
            stdscr.addstr(1, 2, "Changed files since last post"[: w - 4], curses.color_pair(1) | curses.A_BOLD)
            stdscr.addstr(2, 2, "Enter: show diff/details | r: refresh | q: back"[: w - 4])

            if not summaries:
                stdscr.addstr(4, 2, "No changed files found since last post."[: w - 4])
            else:
                view_height = max(1, h - 7)
                top = max(0, selected - view_height + 1)
                for row, item in enumerate(summaries[top: top + view_height], start=4):
                    idx = top + row - 4
                    marker = ">" if idx == selected else " "
                    line = f"{marker} {item['modified']} [{item['kind']}] {item['display_path']} ({item['size_bytes']} bytes)"
                    attr = curses.A_REVERSE if idx == selected else curses.A_NORMAL
                    stdscr.addstr(row, 2, line[: w - 4], attr)

            self.draw_footer(stdscr)
            key = stdscr.getch()
            if key in (ord("q"), 27):
                return
            if key in (ord("r"), ord("R")):
                try:
                    refresh()
                    self.message = "Changed files refreshed."
                except Exception as exc:  # noqa: BLE001
                    self.message = f"Refresh failed: {exc}"
            elif key in (curses.KEY_DOWN, ord("j")) and summaries:
                selected = min(len(summaries) - 1, selected + 1)
            elif key in (curses.KEY_UP, ord("k")) and summaries:
                selected = max(0, selected - 1)
            elif key in (10, 13, curses.KEY_ENTER) and summaries:
                self.show_changed_file_details(stdscr, Path(str(summaries[selected]["path"])))

    def show_changed_file_details(self, stdscr: curses.window, path: Path) -> None:
        try:
            settings = core.get_settings()
            text = core.changed_file_details(path, settings)
        except Exception as exc:  # noqa: BLE001
            text = f"Error: {exc}"
        self.show_text(stdscr, text.splitlines())

    def generate_preview(self, stdscr: curses.window) -> None:
        self.message = "Generating preview..."
        self.draw_footer(stdscr)
        stdscr.refresh()
        try:
            settings = core.get_settings()
            text = core.generate_post(settings, mode="activity")
            path = core.save_draft(text)
            lines = [text, "", "---", f"Saved draft: {path}", f"Chars: {len(text)}"]
            self.message = f"Preview generated and saved: {path.name}"
        except Exception as exc:  # noqa: BLE001
            lines = [f"Error: {exc}"]
            self.message = "Preview failed."
        self.show_text(stdscr, "\n".join(lines).splitlines())

    def topic_post_menu(self, stdscr: curses.window) -> None:
        topic = self.prompt(stdscr, "Тема/мысль для поста: ")
        if not topic.strip():
            self.message = "Пост по теме отменен."
            return
        confirm = self.prompt(stdscr, "Опубликовать пост по теме сейчас? Напиши YES: ")
        if confirm != "YES":
            self.message = "Публикация поста по теме отменена."
            return
        try:
            args = [python_executable(), "-m", "daily_poster", "topic-post", topic]
            result = subprocess.run(
                args,
                cwd=str(core.PROJECT_ROOT),
                check=False,
                capture_output=True,
                text=True,
                timeout=180,
            )
            output = (result.stdout + "\n" + result.stderr).strip()
            self.message = "Пост по теме готов." if result.returncode == 0 else "Пост по теме не удался."
            self.show_text(stdscr, output.splitlines() or ["Нет вывода."])
        except Exception as exc:  # noqa: BLE001
            self.message = f"Пост по теме не удался: {exc}"

    def show_memory(self, stdscr: curses.window) -> None:
        try:
            text = core.memory_context()
        except Exception as exc:  # noqa: BLE001
            text = f"Error: {exc}"
        self.show_text(stdscr, text.splitlines())

    def run_cli_and_show(self, stdscr: curses.window, args: list[str]) -> None:
        try:
            result = subprocess.run(
                [python_executable(), "-m", "daily_poster", *args],
                cwd=str(core.PROJECT_ROOT),
                check=False,
                capture_output=True,
                text=True,
                timeout=240,
            )
            output = (result.stdout + "\n" + result.stderr).strip()
            self.message = "Команда выполнена." if result.returncode == 0 else "Команда завершилась с ошибкой."
            self.show_text(stdscr, output.splitlines() or ["Нет вывода."])
        except Exception as exc:  # noqa: BLE001
            self.message = f"Команда не выполнена: {exc}"

    def install_command(self, _stdscr: curses.window) -> None:
        if IS_WINDOWS:
            self.message = "На Windows команда tgauto появляется после `py -m pip install -e .`."
            return
        source = core.PROJECT_ROOT / "bin" / "tgauto"
        target = Path("/usr/local/bin/tgauto")
        try:
            if target.exists() or target.is_symlink():
                target.unlink()
            target.symlink_to(source)
            self.message = f"Installed command: {target} -> {source}"
        except OSError as exc:
            user_target = Path.home() / "bin" / "tgauto"
            user_target.parent.mkdir(parents=True, exist_ok=True)
            if user_target.exists() or user_target.is_symlink():
                user_target.unlink()
            user_target.symlink_to(source)
            ensure_user_bin_in_zshrc()
            self.message = f"Installed to {user_target}. Open a new terminal if tgauto is not found yet. /usr/local/bin failed: {exc}"

    def prompt(self, stdscr: curses.window, prompt: str, hidden: bool = False) -> str:
        curses.echo(False if hidden else True)
        set_cursor(1)
        stdscr.erase()
        stdscr.addstr(1, 2, prompt)
        stdscr.refresh()

        value = ""
        while True:
            key = stdscr.get_wch()
            if key in ("\n", "\r") or key == curses.KEY_ENTER:
                break
            if key == "\x1b":
                value = ""
                break
            if key in (curses.KEY_BACKSPACE, "\x7f", "\b"):
                value = value[:-1]
            elif isinstance(key, str) and key.isprintable():
                value += key

            stdscr.move(1, 2 + len(prompt))
            stdscr.clrtoeol()
            display = "*" * len(value) if hidden else value
            stdscr.addstr(1, 2 + len(prompt), display)
            stdscr.refresh()

        curses.echo(False)
        set_cursor(0)
        return value

    def show_text(self, stdscr: curses.window, lines: list[str]) -> None:
        offset = 0
        while True:
            stdscr.erase()
            h, w = stdscr.getmaxyx()
            stdscr.addstr(1, 2, "Press q/Esc to return, ↑/↓ to scroll."[: w - 4], curses.color_pair(1))
            view_height = h - 5
            for idx, line in enumerate(lines[offset: offset + view_height]):
                stdscr.addstr(3 + idx, 2, line[: w - 4])
            self.draw_footer(stdscr)
            key = stdscr.getch()
            if key in (ord("q"), 27):
                return
            if key in (curses.KEY_DOWN, ord("j")):
                offset = min(max(0, len(lines) - view_height), offset + 1)
            elif key in (curses.KEY_UP, ord("k")):
                offset = max(0, offset - 1)
            elif key == curses.KEY_NPAGE:
                offset = min(max(0, len(lines) - view_height), offset + view_height)
            elif key == curses.KEY_PPAGE:
                offset = max(0, offset - view_height)


def read_env() -> dict[str, str]:
    core.load_dotenv(core.ENV_PATH)
    env: dict[str, str] = {}
    if core.ENV_PATH.exists():
        for raw_line in core.ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def write_env(values: dict[str, str]) -> None:
    ordered_keys = [
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "ACTIVE_MODE",
        "POST_LANGUAGE",
        "POST_MAX_CHARS",
        "POST_TEMPERATURE",
        "SPONTANEOUS_ENABLED",
        "SPONTANEOUS_CHECK_INTERVAL_MINUTES",
        "SPONTANEOUS_MIN_PAUSE_MINUTES",
        "AUTOPILOT_ENABLED",
        "AUTOPILOT_POSTS_PER_DAY",
        "AUTOPILOT_MIN_PAUSE_MINUTES",
        "GROUP_CHAT_ENABLED",
        "GROUP_CHAT_ID",
        "GROUP_TARGET_USER_ID",
        "GROUP_TARGET_USERNAME",
        "GROUP_TARGET_NAME",
        "GROUP_REPLY_PROBABILITY",
        "GROUP_MIN_PAUSE_SECONDS",
        "GROUP_CONTEXT_MESSAGES",
        "ACTIVITY_SCAN_ROOTS",
        "ACTIVITY_EXCLUDE_DIRS",
        "ACTIVITY_MAX_FILES",
        "ACTIVITY_MAX_CHARS_PER_FILE",
        "ACTIVITY_MAX_TOTAL_CHARS",
    ]
    defaults = {
        "OPENAI_MODEL": "gpt-5.1",
        "ACTIVE_MODE": "tracking",
        "POST_LANGUAGE": "ru",
        "POST_MAX_CHARS": "3500",
        "POST_TEMPERATURE": "0.8",
        "SPONTANEOUS_ENABLED": "true",
        "SPONTANEOUS_CHECK_INTERVAL_MINUTES": "60",
        "SPONTANEOUS_MIN_PAUSE_MINUTES": "360",
        "AUTOPILOT_ENABLED": "false",
        "AUTOPILOT_POSTS_PER_DAY": "2",
        "AUTOPILOT_MIN_PAUSE_MINUTES": "240",
        "GROUP_CHAT_ENABLED": "false",
        "GROUP_CHAT_ID": "",
        "GROUP_TARGET_USER_ID": "",
        "GROUP_TARGET_USERNAME": "",
        "GROUP_TARGET_NAME": "",
        "GROUP_REPLY_PROBABILITY": "0.12",
        "GROUP_MIN_PAUSE_SECONDS": "90",
        "GROUP_CONTEXT_MESSAGES": "16",
        "ACTIVITY_SCAN_ROOTS": core.DEFAULT_SCAN_ROOTS,
        "ACTIVITY_EXCLUDE_DIRS": "node_modules,.git,.venv,venv,__pycache__,Library",
        "ACTIVITY_MAX_FILES": "80",
        "ACTIVITY_MAX_CHARS_PER_FILE": "6000",
        "ACTIVITY_MAX_TOTAL_CHARS": "60000",
    }

    merged = {**defaults, **values}
    lines = ["# OpenAI"]
    for key in ("OPENAI_API_KEY", "OPENAI_MODEL"):
        lines.append(f"{key}={merged.get(key, '')}")
    lines.extend(["", "# Telegram"])
    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        lines.append(f"{key}={merged.get(key, '')}")
    lines.extend(["", "# Product mode"])
    lines.append(f"ACTIVE_MODE={merged.get('ACTIVE_MODE', '')}")
    lines.extend(["", "# Posting"])
    for key in ("POST_LANGUAGE", "POST_MAX_CHARS", "POST_TEMPERATURE"):
        lines.append(f"{key}={merged.get(key, '')}")
    lines.extend(["", "# Spontaneous posting"])
    for key in ("SPONTANEOUS_ENABLED", "SPONTANEOUS_CHECK_INTERVAL_MINUTES", "SPONTANEOUS_MIN_PAUSE_MINUTES"):
        lines.append(f"{key}={merged.get(key, '')}")
    lines.extend(["", "# Channel autopilot"])
    for key in ("AUTOPILOT_ENABLED", "AUTOPILOT_POSTS_PER_DAY", "AUTOPILOT_MIN_PAUSE_MINUTES"):
        lines.append(f"{key}={merged.get(key, '')}")
    lines.extend(["", "# Group chat mode"])
    for key in (
        "GROUP_CHAT_ENABLED",
        "GROUP_CHAT_ID",
        "GROUP_TARGET_USER_ID",
        "GROUP_TARGET_USERNAME",
        "GROUP_TARGET_NAME",
        "GROUP_REPLY_PROBABILITY",
        "GROUP_MIN_PAUSE_SECONDS",
        "GROUP_CONTEXT_MESSAGES",
    ):
        lines.append(f"{key}={merged.get(key, '')}")
    lines.extend(["", "# Daily activity scan"])
    for key in ordered_keys[22:]:
        lines.append(f"{key}={merged.get(key, '')}")
    core.ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def mask(value: str) -> str:
    if not value:
        return "not set"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def set_cursor(mode: int) -> None:
    try:
        curses.curs_set(mode)
    except curses.error:
        pass


def init_colors() -> None:
    try:
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_CYAN, -1)
        curses.init_pair(2, curses.COLOR_GREEN, -1)
        curses.init_pair(3, curses.COLOR_YELLOW, -1)
        curses.init_pair(4, curses.COLOR_RED, -1)
    except curses.error:
        pass


def read_schedule() -> tuple[int, int] | None:
    if not IS_MACOS:
        return None
    path = LAUNCH_AGENT_PATH if LAUNCH_AGENT_PATH.exists() else core.PROJECT_ROOT / "automation" / "com.codex.daily-poster.plist"
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    hour = value_after_key(text, "Hour")
    minute = value_after_key(text, "Minute")
    if hour is None or minute is None:
        return None
    return hour, minute


def value_after_key(text: str, key: str) -> int | None:
    marker = f"<key>{key}</key>"
    idx = text.find(marker)
    if idx == -1:
        return None
    start = text.find("<integer>", idx)
    end = text.find("</integer>", start)
    if start == -1 or end == -1:
        return None
    try:
        return int(text[start + len("<integer>"): end].strip())
    except ValueError:
        return None


def update_plist_schedule(path: Path, hour: int, minute: int) -> None:
    text = path.read_text(encoding="utf-8")
    text = replace_value_after_key(text, "Hour", hour)
    text = replace_value_after_key(text, "Minute", minute)
    path.write_text(text, encoding="utf-8")


def replace_value_after_key(text: str, key: str, value: int) -> str:
    marker = f"<key>{key}</key>"
    idx = text.find(marker)
    if idx == -1:
        return text
    start = text.find("<integer>", idx)
    end = text.find("</integer>", start)
    if start == -1 or end == -1:
        return text
    return text[: start + len("<integer>")] + str(value) + text[end:]


def reload_launch_agent(path: Path = LAUNCH_AGENT_PATH) -> None:
    if not IS_MACOS:
        return
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(path)], check=False, capture_output=True)
    subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(path)], check=False, capture_output=True)


def unload_launch_agent(path: Path) -> None:
    if not IS_MACOS or not path.exists():
        return
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(path)], check=False, capture_output=True)


def install_launch_agent(source: Path, destination: Path) -> None:
    if not IS_MACOS:
        return
    env = read_env()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as handle:
        data = plistlib.load(handle)
    data["WorkingDirectory"] = str(core.PROJECT_ROOT)
    data["ProgramArguments"] = [python_executable(), "-m", "daily_poster", *data.get("ProgramArguments", [])[3:]]
    if destination == MAYBE_AGENT_PATH:
        interval = int(env.get("SPONTANEOUS_CHECK_INTERVAL_MINUTES", "60"))
        data["StartInterval"] = max(60, interval * 60)
    with destination.open("wb") as handle:
        plistlib.dump(data, handle, sort_keys=False)
    reload_launch_agent(destination)


def sync_mode_agents(mode: str) -> None:
    if not IS_MACOS:
        return
    if mode == "autogen":
        source = core.PROJECT_ROOT / "automation" / "com.codex.maybe-poster.plist"
        install_launch_agent(source, MAYBE_AGENT_PATH)
    elif mode in {"tracking", "group", "off"}:
        unload_launch_agent(MAYBE_AGENT_PATH)


def stop_all_bot_work() -> int:
    env = read_env()
    env["ACTIVE_MODE"] = "off"
    env["AUTOPILOT_ENABLED"] = "false"
    env["SPONTANEOUS_ENABLED"] = "false"
    env["GROUP_CHAT_ENABLED"] = "false"
    write_env(env)

    for path in (LAUNCH_AGENT_PATH, MAYBE_AGENT_PATH, AUTOPILOT_AGENT_PATH):
        unload_launch_agent(path)

    return kill_bot_processes()


def kill_bot_processes() -> int:
    if IS_WINDOWS:
        command = (
            "$self=$PID; "
            "Get-CimInstance Win32_Process | "
            "Where-Object { $_.CommandLine -match 'daily_poster' -and "
            "$_.CommandLine -notmatch 'daily_poster.tui' -and $_.ProcessId -ne $self } | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force; $_.ProcessId }"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            check=False,
            capture_output=True,
            text=True,
        )
        return len([line for line in result.stdout.splitlines() if line.strip()])

    try:
        result = subprocess.run(["ps", "-axo", "pid=,command="], check=False, capture_output=True, text=True)
    except OSError:
        return 0

    current_pid = os.getpid()
    killed = 0
    commands = (
        "-m daily_poster autopilot",
        "-m daily_poster maybe-post",
        "-m daily_poster publish",
        "-m daily_poster preview",
        "-m daily_poster topic-post",
        "-m daily_poster send-file",
        "-m daily_poster group-chat",
    )
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        command = parts[1]
        if pid == current_pid or not any(marker in command for marker in commands):
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            killed += 1
        except OSError:
            continue
    return killed


def ensure_user_bin_in_zshrc() -> None:
    zshrc = Path.home() / ".zshrc"
    line = 'export PATH="$HOME/bin:$PATH"'
    if zshrc.exists():
        text = zshrc.read_text(encoding="utf-8")
        if line in text:
            return
        zshrc.write_text(text.rstrip() + "\n\n# Added by tgauto installer\n" + line + "\n", encoding="utf-8")
    else:
        zshrc.write_text("# Added by tgauto installer\n" + line + "\n", encoding="utf-8")


def python_executable() -> str:
    if IS_WINDOWS:
        venv_python = core.PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
    else:
        venv_python = core.PROJECT_ROOT / ".venv" / "bin" / "python"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable or ("py" if IS_WINDOWS else "python3")


def schedule_status() -> str:
    if IS_MACOS:
        schedule = read_schedule()
        return f"{schedule[0]:02d}:{schedule[1]:02d}" if schedule else "не настроен"
    if IS_WINDOWS:
        return "Task Scheduler"
    if IS_LINUX:
        return "cron/systemd"
    return platform.system()


def backup_prompt() -> Path:
    core.DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = core.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    backup = core.DRAFTS_DIR / f"post_prompt_backup_{stamp}.md"
    if core.PROMPT_PATH.exists():
        shutil.copy2(core.PROMPT_PATH, backup)
    else:
        backup.write_text("", encoding="utf-8")
    return backup


if __name__ == "__main__":
    run()
