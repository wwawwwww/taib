from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import random
import re
import sys
import textwrap
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"
PROMPT_PATH = PROJECT_ROOT / "config" / "post_prompt.md"
DRAFTS_DIR = PROJECT_ROOT / "data" / "drafts"
HISTORY_PATH = PROJECT_ROOT / "data" / "posts.jsonl"
STATE_PATH = PROJECT_ROOT / "data" / "state.json"
MEMORY_PATH = PROJECT_ROOT / "data" / "memory.json"
SNAPSHOTS_DIR = PROJECT_ROOT / "data" / "snapshots"
LOGS_DIR = PROJECT_ROOT / "logs"
DEBUG_DIR = PROJECT_ROOT / "data" / "debug"

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
TELEGRAM_SEND_MESSAGE_URL = "https://api.telegram.org/bot{token}/sendMessage"
TELEGRAM_MAX_MESSAGE_CHARS = 4096
DEFAULT_SCAN_ROOTS = "~/Documents,~/Desktop,~/Downloads"
DEFAULT_EXCLUDE_DIRS = {
    ".cache",
    ".git",
    ".idea",
    ".next",
    ".npm",
    ".pnpm-store",
    ".venv",
    ".vscode",
    "__pycache__",
    "Library",
    "node_modules",
    "venv",
}
DEFAULT_EXCLUDE_FILE_NAMES = {
    ".env",
    ".env.local",
    ".netrc",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
}
DEFAULT_EXCLUDE_SUFFIXES = {
    ".7z",
    ".app",
    ".avi",
    ".bin",
    ".db",
    ".dmg",
    ".exe",
    ".gif",
    ".heic",
    ".ico",
    ".jpeg",
    ".jpg",
    ".lock",
    ".mov",
    ".mp3",
    ".mp4",
    ".pdf",
    ".png",
    ".sqlite",
    ".webp",
    ".zip",
}
LOW_SIGNAL_KINDS = {"download", "binary"}
CODE_SUFFIXES = {
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".css",
    ".go",
    ".h",
    ".hpp",
    ".html",
    ".java",
    ".js",
    ".jsx",
    ".kt",
    ".m",
    ".mm",
    ".php",
    ".py",
    ".rb",
    ".rs",
    ".scala",
    ".scss",
    ".sh",
    ".sql",
    ".swift",
    ".ts",
    ".tsx",
    ".vue",
}
SENSITIVE_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
    re.compile(r"(?i)(api[_-]?key|token|password|secret)\s*[:=]\s*['\"]?[^'\"\s]+"),
]


class ConfigError(RuntimeError):
    pass


class ApiError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    openai_api_key: str
    openai_model: str
    telegram_bot_token: str
    telegram_chat_id: str
    post_language: str
    post_max_chars: int
    post_temperature: float
    activity_scan_roots: list[Path]
    activity_exclude_dirs: set[str]
    activity_max_files: int
    activity_max_chars_per_file: int
    activity_max_total_chars: int
    spontaneous_enabled: bool
    spontaneous_min_pause_hours: int


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def get_settings() -> Settings:
    load_dotenv(ENV_PATH)

    missing = [
        name
        for name in ("OPENAI_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
        if not os.environ.get(name)
    ]
    if missing:
        joined = ", ".join(missing)
        raise ConfigError(f"Не хватает переменных окружения: {joined}. Проверь .env")

    try:
        max_chars = int(os.environ.get("POST_MAX_CHARS", "3500"))
    except ValueError as exc:
        raise ConfigError("POST_MAX_CHARS должен быть числом.") from exc

    try:
        temperature = float(os.environ.get("POST_TEMPERATURE", "0.8"))
    except ValueError as exc:
        raise ConfigError("POST_TEMPERATURE должен быть числом.") from exc

    if max_chars <= 0 or max_chars > TELEGRAM_MAX_MESSAGE_CHARS:
        raise ConfigError(f"POST_MAX_CHARS должен быть от 1 до {TELEGRAM_MAX_MESSAGE_CHARS}.")

    try:
        activity_max_files = int(os.environ.get("ACTIVITY_MAX_FILES", "80"))
        activity_max_chars_per_file = int(os.environ.get("ACTIVITY_MAX_CHARS_PER_FILE", "6000"))
        activity_max_total_chars = int(os.environ.get("ACTIVITY_MAX_TOTAL_CHARS", "60000"))
        spontaneous_min_pause_hours = int(os.environ.get("SPONTANEOUS_MIN_PAUSE_HOURS", "6"))
    except ValueError as exc:
        raise ConfigError("ACTIVITY_* и SPONTANEOUS_MIN_PAUSE_HOURS должны быть числами.") from exc

    scan_roots = parse_paths(os.environ.get("ACTIVITY_SCAN_ROOTS", DEFAULT_SCAN_ROOTS))
    exclude_dirs = DEFAULT_EXCLUDE_DIRS | parse_csv_set(os.environ.get("ACTIVITY_EXCLUDE_DIRS", ""))
    spontaneous_enabled = parse_bool(os.environ.get("SPONTANEOUS_ENABLED", "true"))

    return Settings(
        openai_api_key=os.environ["OPENAI_API_KEY"],
        openai_model=os.environ.get("OPENAI_MODEL", "gpt-5.1"),
        telegram_bot_token=os.environ["TELEGRAM_BOT_TOKEN"],
        telegram_chat_id=os.environ["TELEGRAM_CHAT_ID"],
        post_language=os.environ.get("POST_LANGUAGE", "ru"),
        post_max_chars=max_chars,
        post_temperature=temperature,
        activity_scan_roots=scan_roots,
        activity_exclude_dirs=exclude_dirs,
        activity_max_files=activity_max_files,
        activity_max_chars_per_file=activity_max_chars_per_file,
        activity_max_total_chars=activity_max_total_chars,
        spontaneous_enabled=spontaneous_enabled,
        spontaneous_min_pause_hours=spontaneous_min_pause_hours,
    )


def parse_csv_set(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}


def parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y", "on", "да"}


def parse_paths(value: str) -> list[Path]:
    paths: list[Path] = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        path = Path(item).expanduser().resolve()
        if path.exists():
            paths.append(path)
    if not paths:
        raise ConfigError("ACTIVITY_SCAN_ROOTS не содержит существующих папок.")
    return paths


def read_prompt() -> str:
    if not PROMPT_PATH.exists():
        raise ConfigError(f"Не найден файл промпта: {PROMPT_PATH}")
    return PROMPT_PATH.read_text(encoding="utf-8").strip()


def read_recent_posts(limit: int = 8) -> list[str]:
    if not HISTORY_PATH.exists():
        return []

    rows = HISTORY_PATH.read_text(encoding="utf-8").splitlines()
    posts: list[str] = []
    for row in rows[-limit:]:
        try:
            item = json.loads(row)
        except json.JSONDecodeError:
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            posts.append(text.strip())
    return posts


def read_memory() -> dict[str, Any]:
    if not MEMORY_PATH.exists():
        return {
            "topics": {},
            "recent_posts": [],
            "style_notes": [
                "Писать от первого лица, живо и без упоминания автоматизации.",
                "Не начинать каждый пост одинаково.",
            ],
        }
    try:
        memory = json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"topics": {}, "recent_posts": [], "style_notes": []}
    return memory if isinstance(memory, dict) else {"topics": {}, "recent_posts": [], "style_notes": []}


def write_memory(memory: dict[str, Any]) -> None:
    ensure_dirs()
    MEMORY_PATH.write_text(json.dumps(memory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def keyword_candidates(text: str, limit: int = 12) -> list[str]:
    words = re.findall(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9_+-]{3,}", text.lower())
    stop_words = {
        "сегодня", "который", "которая", "которые", "потому", "просто", "можно", "очень",
        "через", "после", "перед", "если", "когда", "тогда", "вообще", "немного",
        "this", "that", "with", "from", "into", "about", "there", "their", "would",
    }
    counts: dict[str, int] = {}
    for word in words:
        if word in stop_words:
            continue
        counts[word] = counts.get(word, 0) + 1
    return [word for word, _count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]]


def update_memory(text: str, kind: str, topic: str | None = None) -> None:
    memory = read_memory()
    topics = memory.setdefault("topics", {})
    recent_posts = memory.setdefault("recent_posts", [])

    keys = keyword_candidates(" ".join([topic or "", text]))
    for key in keys:
        item = topics.setdefault(key, {"count": 0, "last_seen": None})
        item["count"] = int(item.get("count", 0)) + 1
        item["last_seen"] = datetime.now().isoformat(timespec="seconds")

    recent_posts.append(
        {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "kind": kind,
            "topic": topic,
            "keywords": keys[:8],
            "excerpt": text[:500],
        }
    )
    memory["recent_posts"] = recent_posts[-20:]
    write_memory(memory)


def memory_context() -> str:
    memory = read_memory()
    topics = memory.get("topics", {})
    recent_posts = memory.get("recent_posts", [])
    style_notes = memory.get("style_notes", [])

    topic_lines: list[str] = []
    if isinstance(topics, dict):
        sorted_topics = sorted(
            topics.items(),
            key=lambda item: (-int(item[1].get("count", 0)) if isinstance(item[1], dict) else 0, item[0]),
        )
        for topic, data in sorted_topics[:20]:
            count = data.get("count", 0) if isinstance(data, dict) else 0
            last_seen = data.get("last_seen", "") if isinstance(data, dict) else ""
            topic_lines.append(f"- {topic}: {count} posts, last {last_seen}")

    recent_lines: list[str] = []
    if isinstance(recent_posts, list):
        for item in recent_posts[-8:]:
            if not isinstance(item, dict):
                continue
            topic = item.get("topic") or "(без темы)"
            keywords = ", ".join(item.get("keywords", [])[:6]) if isinstance(item.get("keywords"), list) else ""
            excerpt = str(item.get("excerpt", "")).replace("\n", " ")[:220]
            recent_lines.append(f"- {item.get('created_at', '')} | {item.get('kind', '')} | {topic} | {keywords} | {excerpt}")

    style_block = "\n".join(f"- {note}" for note in style_notes) if style_notes else "- нет"
    topics_block = "\n".join(topic_lines) if topic_lines else "- пока нет"
    recent_block = "\n".join(recent_lines) if recent_lines else "- пока нет"
    return (
        "Локальная память канала:\n\n"
        f"Стилевые заметки:\n{style_block}\n\n"
        f"Частые темы:\n{topics_block}\n\n"
        f"Последние смысловые следы постов:\n{recent_block}"
    )


def make_generation_input(
    settings: Settings,
    editorial_prompt: str,
    recent_posts: list[str],
    activity_context: str,
    mode: str = "activity",
) -> str:
    recent_block = "\n\n---\n\n".join(recent_posts) if recent_posts else "Истории пока нет."
    memory_block = memory_context()
    today = datetime.now().strftime("%Y-%m-%d")
    if mode == "free":
        return textwrap.dedent(
            f"""
            Сегодня: {today}
            Язык поста: {settings.post_language}
            Максимальная длина: {settings.post_max_chars} символов.

            Редакционная политика:
            {editorial_prompt}

            Недавние посты, чтобы не повторяться:
            {recent_block}

            {memory_block}

            Важно: недавние посты нужны только чтобы не повторяться. Не копируй их формулировки, особенно "ежедневный отчет", "по файлам видно", "чекпойнт", "файловая активность".

            Режим: свободный авторский пост.
            Напиши короткий живой пост от первого лица, будто мысль сама дозрела и захотелось ей поделиться.
            Можно писать про программирование, учебу, Scala, AI, продуктивность, маленькое наблюдение из работы или идею на будущее.
            Не выдумывай конкретную сегодняшнюю работу, баги, тесты, задачи или файлы.
            Лучше пиши как наблюдение: "Иногда в Scala...", "Есть странная ловушка...", "Мне нравится идея...".
            Не упоминай файлы, сканирование, бота, модель, автоматизацию, отчетность или то, что пост был сгенерирован.
            Верни только текст поста, без пояснений.
            """
        ).strip()
    if mode == "topic":
        return textwrap.dedent(
            f"""
            Сегодня: {today}
            Язык поста: {settings.post_language}
            Максимальная длина: {settings.post_max_chars} символов.

            Редакционная политика:
            {editorial_prompt}

            Недавние посты, чтобы не повторяться:
            {recent_block}

            {memory_block}

            Тема/мысль от автора:
            {activity_context}

            Напиши пост строго на эту тему, на русском языке, от первого лица, как Артем.
            Развей мысль так, чтобы это было похоже на живой авторский пост в канал, а не на ответ ассистента.
            Не упоминай промпт, тему как техническое задание, бота, модель, файлы или автоматизацию.
            Верни только текст поста, без пояснений.
            """
        ).strip()

    return textwrap.dedent(
        f"""
        Сегодня: {today}
        Язык поста: {settings.post_language}
        Максимальная длина: {settings.post_max_chars} символов.

        Редакционная политика:
        {editorial_prompt}

        Недавние посты, чтобы не повторяться:
        {recent_block}

        {memory_block}

        Важно: недавние посты нужны только чтобы не повторяться. Не копируй их формулировки, особенно "ежедневный отчет", "по файлам видно", "чекпойнт", "файловая активность".

        Рабочий контекст с прошлого поста:
        {activity_context}

        Напиши один новый авторский пост для Telegram-канала на основе рабочего контекста.
        Если по файлам нельзя уверенно понять смысл задачи, так и напиши мягко и без выдумывания.
        Если изменений почти нет или есть только слабые сигналы вроде скачанной картинки, честно отрази спокойный/не особо продуктивный день.
        Пиши от первого лица как Артем: "я разбирался", "у меня заняло", "сегодня получилось".
        Не говори "Артем сделал", не упоминай сканирование файлов, контекст, бота, модель, автоматизацию или ежедневный отчет.
        Верни только текст поста, без пояснений.
        """
    ).strip()


def today_bounds() -> tuple[float, float]:
    today = date.today()
    start = datetime.combine(today, time.min).timestamp()
    end = datetime.combine(today, time.max).timestamp()
    return start, end


def read_checkpoint() -> float:
    state = read_state()
    value = state.get("last_post_checkpoint")
    if not isinstance(value, str):
        return today_bounds()[0]
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return today_bounds()[0]


def write_checkpoint(moment: datetime | None = None) -> None:
    ensure_dirs()
    moment = moment or datetime.now()
    state = read_state()
    state["last_post_checkpoint"] = moment.isoformat(timespec="seconds")
    write_state(state)


def checkpoint_label() -> str:
    return datetime.fromtimestamp(read_checkpoint()).strftime("%Y-%m-%d %H:%M:%S")


def read_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {}
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return state if isinstance(state, dict) else {}


def write_state(state: dict[str, Any]) -> None:
    ensure_dirs()
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_last_any_post_time() -> float:
    state = read_state()
    value = state.get("last_any_post")
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).timestamp()
        except ValueError:
            pass
    if HISTORY_PATH.exists():
        for row in reversed(HISTORY_PATH.read_text(encoding="utf-8").splitlines()):
            try:
                item = json.loads(row)
            except json.JSONDecodeError:
                continue
            created_at = item.get("created_at")
            if isinstance(created_at, str):
                try:
                    return datetime.fromisoformat(created_at).timestamp()
                except ValueError:
                    continue
    return 0.0


def write_last_any_post(kind: str, moment: datetime | None = None) -> None:
    moment = moment or datetime.now()
    state = read_state()
    state["last_any_post"] = moment.isoformat(timespec="seconds")
    state["last_post_kind"] = kind
    write_state(state)


def hours_since_last_post() -> float:
    last = read_last_any_post_time()
    if last <= 0:
        return 10_000.0
    return max(0.0, (datetime.now().timestamp() - last) / 3600)


def is_probably_text(path: Path, max_probe_bytes: int = 4096) -> bool:
    try:
        chunk = path.read_bytes()[:max_probe_bytes]
    except OSError:
        return False
    if b"\x00" in chunk:
        return False
    try:
        chunk.decode("utf-8")
    except UnicodeDecodeError:
        try:
            chunk.decode("latin-1")
        except UnicodeDecodeError:
            return False
    return True


def should_skip_file(path: Path, settings: Settings) -> bool:
    resolved = path.resolve()
    if resolved == HISTORY_PATH.resolve() or resolved == STATE_PATH.resolve() or resolved == ENV_PATH.resolve():
        return True
    if LOGS_DIR.resolve() in resolved.parents:
        return True
    if SNAPSHOTS_DIR.resolve() in resolved.parents or DRAFTS_DIR.resolve() in resolved.parents:
        return True
    if path.name.startswith("."):
        return True
    if path.name in DEFAULT_EXCLUDE_FILE_NAMES:
        return True
    if path.suffix.lower() in DEFAULT_EXCLUDE_SUFFIXES and not is_download_path(path):
        return True
    if any(part in settings.activity_exclude_dirs for part in path.parts):
        return True
    lowered = path.name.lower()
    sensitive_markers = ("secret", "token", "credential", "private_key", "password")
    return any(marker in lowered for marker in sensitive_markers)


def is_download_path(path: Path) -> bool:
    downloads = (Path.home() / "Downloads").resolve()
    try:
        path.resolve().relative_to(downloads)
        return True
    except ValueError:
        return False


def iter_changed_files(settings: Settings) -> list[Path]:
    start = read_checkpoint()
    end = datetime.now().timestamp()
    candidates: list[tuple[float, Path]] = []

    for root in settings.activity_scan_roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                name for name in dirnames
                if name not in settings.activity_exclude_dirs and not name.startswith(".")
            ]

            current_dir = Path(dirpath)
            for filename in filenames:
                path = current_dir / filename
                if should_skip_file(path, settings):
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                if start <= stat.st_mtime <= end and stat.st_size > 0:
                    candidates.append((stat.st_mtime, path))

    candidates.sort(key=lambda item: item[0], reverse=True)
    return [path for _, path in candidates[: settings.activity_max_files]]


def changed_file_summaries(settings: Settings) -> list[dict[str, str | int]]:
    summaries: list[dict[str, str | int]] = []
    for path in iter_changed_files(settings):
        try:
            stat = path.stat()
        except OSError:
            continue
        kind = file_kind(path)
        summaries.append(
            {
                "path": str(path),
                "display_path": display_path(path),
                "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%H:%M"),
                "size_bytes": stat.st_size,
                "kind": kind,
            }
        )
    return summaries


def activity_stats(settings: Settings) -> dict[str, Any]:
    summaries = changed_file_summaries(settings)
    by_kind: dict[str, int] = {}
    total_bytes = 0
    for item in summaries:
        kind = str(item["kind"])
        by_kind[kind] = by_kind.get(kind, 0) + 1
        total_bytes += int(item["size_bytes"])

    strong_count = sum(count for kind, count in by_kind.items() if kind not in LOW_SIGNAL_KINDS)
    weak_count = sum(count for kind, count in by_kind.items() if kind in LOW_SIGNAL_KINDS)
    score = min(100, strong_count * 18 + weak_count * 4)
    if not summaries:
        tone = "quiet"
    elif strong_count == 0:
        tone = "low-signal"
    elif score >= 60:
        tone = "productive"
    else:
        tone = "moderate"

    return {
        "checkpoint": checkpoint_label(),
        "files": len(summaries),
        "strong_files": strong_count,
        "weak_files": weak_count,
        "by_kind": by_kind,
        "total_bytes": total_bytes,
        "score": score,
        "tone": tone,
    }


def activity_digest(settings: Settings) -> str:
    stats = activity_stats(settings)
    by_kind = ", ".join(f"{kind}: {count}" for kind, count in sorted(stats["by_kind"].items())) or "none"
    return textwrap.dedent(
        f"""
        Activity digest since last post
        Checkpoint: {stats["checkpoint"]}
        Files: {stats["files"]}
        Strong signals: {stats["strong_files"]}
        Weak signals: {stats["weak_files"]}
        Kinds: {by_kind}
        Total bytes: {stats["total_bytes"]}
        Score: {stats["score"]}/100
        Tone: {stats["tone"]}
        """
    ).strip()


def file_metadata(path: Path) -> dict[str, str | int]:
    stat = path.stat()
    created_ts = getattr(stat, "st_birthtime", stat.st_ctime)
    return {
        "path": str(path),
        "display_path": display_path(path),
        "name": path.name,
        "stem": path.stem,
        "extension": path.suffix or "(нет)",
        "kind": file_kind(path),
        "size_bytes": stat.st_size,
        "created": datetime.fromtimestamp(created_ts).strftime("%Y-%m-%d %H:%M:%S"),
        "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
    }


def metadata_block(path: Path) -> str:
    metadata = file_metadata(path)
    return textwrap.dedent(
        f"""
        FILE: {metadata["display_path"]}
        NAME: {metadata["name"]}
        KIND: {metadata["kind"]}
        EXTENSION: {metadata["extension"]}
        SIZE_BYTES: {metadata["size_bytes"]}
        CREATED: {metadata["created"]}
        MODIFIED: {metadata["modified"]}
        CONTENT_EXCERPT:
        Содержимое не прочитано, потому что файл не является текстовым. Используй имя, расширение, размер и даты как сигнал активности.
        """
    ).strip()


def file_kind(path: Path) -> str:
    if is_download_path(path) and not is_probably_text(path):
        return "download"
    if path.suffix.lower() in CODE_SUFFIXES:
        return "code"
    if is_probably_text(path):
        return "text"
    return "binary"


def display_path(path: Path) -> str:
    try:
        rel_path = path.relative_to(Path.home())
        return f"~/{rel_path}"
    except ValueError:
        return str(path)


def find_git_root(path: Path) -> Path | None:
    current = path if path.is_dir() else path.parent
    while True:
        if (current / ".git").exists():
            return current
        if current == current.parent:
            return None
        current = current.parent


def git_diff_for_file(path: Path, max_chars: int = 30000) -> str | None:
    git_root = find_git_root(path)
    if git_root is None:
        return None
    try:
        relative = path.relative_to(git_root)
    except ValueError:
        return None
    result = subprocess.run(
        ["git", "-C", str(git_root), "diff", "--", str(relative)],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    diff = result.stdout.strip()
    if not diff:
        result = subprocess.run(
            ["git", "-C", str(git_root), "diff", "--cached", "--", str(relative)],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        diff = result.stdout.strip()
    if not diff:
        return None
    return redact_sensitive_text(diff[:max_chars])


def changed_file_details(path: Path, settings: Settings) -> str:
    diff = git_diff_for_file(path)
    if diff:
        return f"DIFF: {display_path(path)}\n\n{diff}"

    excerpt = read_text_excerpt(path, settings.activity_max_chars_per_file)
    if excerpt is None:
        try:
            metadata = file_metadata(path)
        except OSError:
            return f"Файл не найден или недоступен: {display_path(path)}"
        return textwrap.dedent(
            f"""
            Текстовый diff недоступен: файл не является читаемым текстовым файлом.

            Файл: {metadata["display_path"]}
            Имя: {metadata["name"]}
            Тип: {metadata["kind"]}
            Расширение: {metadata["extension"]}
            Размер: {metadata["size_bytes"]} bytes
            Создан: {metadata["created"]}
            Изменен: {metadata["modified"]}

            Такой файл будет виден как скачанный/измененный. В OpenAI отправляется только эта метаинформация, без бинарного содержимого файла.
            """
        ).strip()

    snapshot_diff = snapshot_diff_for_file(path, excerpt)
    if snapshot_diff:
        return f"LOCAL DIFF: {display_path(path)}\n\n{snapshot_diff}"

    return textwrap.dedent(
        f"""
        DIFF недоступен: файл не найден в git-репозитории или не имеет незакоммиченных изменений.
        Локального checkpoint-снимка пока нет. Он появится после успешной публикации или ручного checkpoint.

        Текущий текстовый снимок файла: {display_path(path)}

        {excerpt}
        """
    ).strip()


def snapshot_path_for_file(path: Path) -> Path:
    digest = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()
    return SNAPSHOTS_DIR / f"{digest}.txt"


def snapshot_diff_for_file(path: Path, current_text: str, max_chars: int = 30000) -> str | None:
    snapshot_path = snapshot_path_for_file(path)
    if not snapshot_path.exists():
        return None

    try:
        previous_text = snapshot_path.read_text(encoding="utf-8")
    except OSError:
        return None

    if previous_text == current_text:
        return "Локальный снимок совпадает с текущим содержимым. Новых отличий после последнего просмотра нет."

    diff = difflib.unified_diff(
        previous_text.splitlines(),
        current_text.splitlines(),
        fromfile=f"previous/{display_path(path)}",
        tofile=f"current/{display_path(path)}",
        lineterm="",
    )
    return "\n".join(diff)[:max_chars]


def update_snapshot(path: Path, current_text: str) -> None:
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_path_for_file(path).write_text(current_text, encoding="utf-8")


def refresh_snapshots(settings: Settings) -> int:
    count = 0
    for root in settings.activity_scan_roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                name for name in dirnames
                if name not in settings.activity_exclude_dirs and not name.startswith(".")
            ]
            current_dir = Path(dirpath)
            for filename in filenames:
                path = current_dir / filename
                if should_skip_file(path, settings):
                    continue
                excerpt = read_text_excerpt(path, settings.activity_max_chars_per_file)
                if excerpt is None:
                    continue
                update_snapshot(path, excerpt)
                count += 1
    return count


def read_text_excerpt(path: Path, max_chars: int) -> str | None:
    if not is_probably_text(path):
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    text = text.strip()
    if not text:
        return None
    return redact_sensitive_text(text[:max_chars])


def redact_sensitive_text(text: str) -> str:
    redacted = text
    for pattern in SENSITIVE_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def build_activity_context(settings: Settings) -> str:
    files = iter_changed_files(settings)
    digest = activity_digest(settings)
    if not files:
        roots = ", ".join(str(path) for path in settings.activity_scan_roots)
        return textwrap.dedent(
            f"""
            {digest}

            С момента прошлого поста изменений в заданных папках не найдено.
            Checkpoint прошлого поста: {checkpoint_label()}
            Папки сканирования: {roots}

            Для отчета честно отрази, что заметной файловой активности почти не было. Не выдумывай выполненные задачи.
            """
        ).strip()

    blocks: list[str] = []
    total_chars = 0
    for path in files:
        excerpt = read_text_excerpt(path, settings.activity_max_chars_per_file)
        if excerpt is None:
            try:
                block = metadata_block(path)
            except OSError:
                continue
            if total_chars + len(block) > settings.activity_max_total_chars:
                break
            blocks.append(block)
            total_chars += len(block)
            continue
        stat = path.stat()
        modified_at = datetime.fromtimestamp(stat.st_mtime).strftime("%H:%M")
        block = textwrap.dedent(
            f"""
            FILE: {display_path(path)}
            MODIFIED: {modified_at}
            SIZE_BYTES: {stat.st_size}
            CONTENT_EXCERPT:
            {excerpt}
            """
        ).strip()

        if total_chars + len(block) > settings.activity_max_total_chars:
            break
        blocks.append(block)
        total_chars += len(block)

    if not blocks:
        return textwrap.dedent(
            f"""
            С момента прошлого поста найдены изменения, но среди них не оказалось полезных сигналов для чтения.
            Checkpoint прошлого поста: {checkpoint_label()}

            Для отчета честно отрази спокойный/не особо продуктивный день и не выдумывай выполненные задачи.
            """
        ).strip()

    roots = ", ".join(str(path) for path in settings.activity_scan_roots)
    joined_blocks = "\n\n---\n\n".join(blocks)
    low_signal_count = sum(1 for block in blocks if "KIND: download" in block or "KIND: binary" in block)
    signal_note = ""
    if low_signal_count == len(blocks):
        signal_note = (
            "\nСигнал активности слабый: найдены только нечитаемые файлы/загрузки. "
            "Для отчета не выдумывай продуктивную работу; можно написать, что день был спокойным или без заметного прогресса."
        )
    return (
        f"{digest}\n\n"
        f"Checkpoint прошлого поста: {checkpoint_label()}\n"
        f"Папки сканирования: {roots}\n"
        f"Найдено измененных файлов с прошлого поста: {len(blocks)}"
        f"{signal_note}\n\n"
        f"{joined_blocks}"
    )


def post_json(url: str, payload: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise ApiError(f"HTTP {exc.code}: {details}") from exc
    except urllib.error.URLError as exc:
        raise ApiError(f"Ошибка сети: {exc.reason}") from exc

    try:
        parsed = json.loads(data)
    except json.JSONDecodeError as exc:
        raise ApiError(f"API вернул не JSON: {data[:500]}") from exc

    return parsed


def extract_response_text(response: dict[str, Any]) -> str:
    output_text = response.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    chunks: list[str] = []
    collect_text_fields(response, chunks)
    text = "\n".join(chunk.strip() for chunk in chunks if chunk.strip()).strip()
    if text:
        return text

    debug_path = save_debug_response(response)
    status = response.get("status", "unknown")
    incomplete = response.get("incomplete_details") or response.get("error") or {}
    hint = ""
    if status == "incomplete":
        hint = f" Статус ответа: incomplete. Детали: {json.dumps(incomplete, ensure_ascii=False)}."
    raise ApiError(
        "Не удалось извлечь текст из ответа OpenAI."
        f"{hint} Сырой ответ сохранен: {debug_path}"
    )


def collect_text_fields(value: Any, chunks: list[str]) -> None:
    if isinstance(value, dict):
        value_type = value.get("type")
        text = value.get("text")
        if isinstance(text, str) and value_type in {None, "output_text", "text"}:
            chunks.append(text)
        output_text = value.get("output_text")
        if isinstance(output_text, str):
            chunks.append(output_text)
        for nested in value.values():
            collect_text_fields(nested, chunks)
    elif isinstance(value, list):
        for item in value:
            collect_text_fields(item, chunks)


def save_debug_response(response: dict[str, Any]) -> Path:
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    response_id = str(response.get("id", "response")).replace("/", "_")
    path = DEBUG_DIR / f"{stamp}_{response_id}.json"
    path.write_text(json.dumps(response, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def legacy_extract_response_text(response: dict[str, Any]) -> str:
    chunks: list[str] = []
    for item in response.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if not isinstance(content, dict):
                continue
            text = content.get("text")
            if isinstance(text, str):
                chunks.append(text)

    text = "".join(chunks).strip()
    if not text:
        raise ApiError(f"Не удалось извлечь текст из ответа OpenAI: {json.dumps(response)[:1000]}")
    return text


def generate_post(settings: Settings, mode: str = "activity", topic: str | None = None) -> str:
    editorial_prompt = read_prompt()
    if mode == "activity":
        activity_context = build_activity_context(settings)
    elif mode == "topic":
        activity_context = topic or ""
    else:
        activity_context = "Свободный пост без файлового контекста."
    prompt = make_generation_input(settings, editorial_prompt, read_recent_posts(), activity_context, mode=mode)
    payload = {
        "model": settings.openai_model,
        "instructions": "Ты опытный редактор Telegram-канала. Соблюдай редакционную политику строго.",
        "input": prompt,
        "max_output_tokens": 3000,
    }
    if supports_temperature(settings.openai_model):
        payload["temperature"] = settings.post_temperature
    response = post_json(
        OPENAI_RESPONSES_URL,
        payload,
        headers={"Authorization": f"Bearer {settings.openai_api_key}"},
    )
    text = extract_response_text(response)
    validate_post(text, settings.post_max_chars)
    return text


def supports_temperature(model: str) -> bool:
    normalized = model.lower()
    unsupported_prefixes = (
        "gpt-5",
        "o1",
        "o3",
        "o4",
    )
    return not normalized.startswith(unsupported_prefixes)


def validate_post(text: str, max_chars: int) -> None:
    if not text:
        raise ConfigError("Пост пустой.")
    if len(text) > max_chars:
        raise ConfigError(f"Пост получился слишком длинным: {len(text)} символов при лимите {max_chars}.")
    if len(text) > TELEGRAM_MAX_MESSAGE_CHARS:
        raise ConfigError(f"Пост длиннее лимита Telegram: {len(text)} символов.")


def send_to_telegram(settings: Settings, text: str) -> dict[str, Any]:
    validate_post(text, settings.post_max_chars)
    url = TELEGRAM_SEND_MESSAGE_URL.format(token=urllib.parse.quote(settings.telegram_bot_token))
    payload = {
        "chat_id": settings.telegram_chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    response = post_json(url, payload)
    if not response.get("ok"):
        raise ApiError(f"Telegram API вернул ошибку: {json.dumps(response, ensure_ascii=False)}")
    return response


def ensure_dirs() -> None:
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)


def save_draft(text: str) -> Path:
    ensure_dirs()
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    path = DRAFTS_DIR / f"{stamp}.md"
    path.write_text(text + "\n", encoding="utf-8")
    return path


def append_history(text: str, telegram_response: dict[str, Any] | None = None, kind: str = "activity") -> None:
    ensure_dirs()
    record = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "kind": kind,
        "text": text,
        "telegram": telegram_response,
    }
    with HISTORY_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def command_env_check() -> int:
    settings = get_settings()
    read_prompt()
    print("OK: .env прочитан")
    print(f"OpenAI model: {settings.openai_model}")
    print(f"Telegram chat: {settings.telegram_chat_id}")
    print(f"Post max chars: {settings.post_max_chars}")
    print("Activity scan roots:")
    for root in settings.activity_scan_roots:
        print(f"- {root}")
    print(f"Activity max files: {settings.activity_max_files}")
    print(f"Last post checkpoint: {checkpoint_label()}")
    print(f"Spontaneous enabled: {settings.spontaneous_enabled}")
    print(f"Spontaneous min pause hours: {settings.spontaneous_min_pause_hours}")
    print(f"Hours since last post: {hours_since_last_post():.1f}")
    return 0


def command_digest() -> int:
    settings = get_settings()
    print(activity_digest(settings))
    return 0


def command_mark_checkpoint() -> int:
    settings = get_settings()
    write_checkpoint()
    count = refresh_snapshots(settings)
    print(f"Checkpoint updated: {checkpoint_label()}")
    print(f"Text snapshots refreshed: {count}")
    return 0


def command_doctor() -> int:
    problems: list[str] = []
    warnings: list[str] = []
    try:
        settings = get_settings()
    except ConfigError as exc:
        print(f"ERROR: {exc}")
        return 1

    if not PROMPT_PATH.exists():
        problems.append(f"Prompt file missing: {PROMPT_PATH}")
    if not settings.telegram_chat_id.startswith("@") and not settings.telegram_chat_id.startswith("-100"):
        warnings.append("TELEGRAM_CHAT_ID usually starts with @ for public channels or -100 for private channels.")
    if settings.activity_max_total_chars > 120000:
        warnings.append("ACTIVITY_MAX_TOTAL_CHARS is high; posts may become expensive.")
    if any(path == Path.home() for path in settings.activity_scan_roots):
        warnings.append("Scanning the whole home folder can be noisy and more expensive.")

    launch_agent = Path.home() / "Library" / "LaunchAgents" / "com.codex.daily-poster.plist"
    if not launch_agent.exists():
        warnings.append(f"LaunchAgent is not installed: {launch_agent}")

    print("tgauto doctor")
    print(f"Project: {PROJECT_ROOT}")
    print(f"Model: {settings.openai_model}")
    print(f"Channel: {settings.telegram_chat_id}")
    print(f"Checkpoint: {checkpoint_label()}")
    print(f"Last post age: {hours_since_last_post():.1f}h")
    print(f"Spontaneous: {settings.spontaneous_enabled}, min pause {settings.spontaneous_min_pause_hours}h")
    print(activity_digest(settings))

    if problems:
        print("\nProblems:")
        for problem in problems:
            print(f"- {problem}")
    if warnings:
        print("\nWarnings:")
        for warning in warnings:
            print(f"- {warning}")
    if not problems and not warnings:
        print("\nOK: no obvious configuration issues found.")
    return 1 if problems else 0


def command_context() -> int:
    settings = get_settings()
    print(build_activity_context(settings))
    return 0


def command_preview(args: argparse.Namespace) -> int:
    settings = get_settings()
    text = generate_post(settings, mode=args.mode)
    print(text)
    print(f"\n---\nСимволов: {len(text)}")
    if args.save:
        path = save_draft(text)
        print(f"Черновик сохранен: {path}")
    return 0


def choose_maybe_post_mode(settings: Settings) -> str:
    stats = activity_stats(settings)
    if int(stats["strong_files"]) > 0 and int(stats["score"]) >= 18:
        return "activity"
    return "free"


def command_maybe_post(args: argparse.Namespace) -> int:
    settings = get_settings()
    if not settings.spontaneous_enabled and not args.force:
        print("Skipped: spontaneous posting is disabled.")
        return 0

    elapsed = hours_since_last_post()
    if elapsed < settings.spontaneous_min_pause_hours and not args.force:
        print(
            f"Skipped: last post was {elapsed:.1f}h ago; "
            f"minimum pause is {settings.spontaneous_min_pause_hours}h."
        )
        return 0

    mode = args.mode if args.mode != "auto" else choose_maybe_post_mode(settings)
    if mode == "free" and not args.force:
        # A light-touch coin flip gives the bot room to "want" to post without posting every check.
        probability = 0.35
        if random.random() > probability:
            print("Skipped: no strong thought this time.")
            return 0

    text = generate_post(settings, mode=mode)
    response = send_to_telegram(settings, text)
    append_history(text, response, kind=mode)
    update_memory(text, kind=mode)
    write_checkpoint()
    write_last_any_post(mode)
    refresh_snapshots(settings)
    message_id = response.get("result", {}).get("message_id", "unknown")
    print(f"Опубликовано ({mode}). Telegram message_id: {message_id}")
    return 0


def command_publish() -> int:
    settings = get_settings()
    text = generate_post(settings, mode="activity")
    response = send_to_telegram(settings, text)
    append_history(text, response, kind="activity")
    update_memory(text, kind="activity")
    write_checkpoint()
    write_last_any_post("activity")
    refresh_snapshots(settings)
    message_id = response.get("result", {}).get("message_id", "unknown")
    print(f"Опубликовано. Telegram message_id: {message_id}")
    return 0


def command_send_file(args: argparse.Namespace) -> int:
    settings = get_settings()
    path = Path(args.path).expanduser().resolve()
    if not path.exists():
        raise ConfigError(f"Файл не найден: {path}")
    text = path.read_text(encoding="utf-8").strip()
    response = send_to_telegram(settings, text)
    append_history(text, response, kind="manual")
    update_memory(text, kind="manual")
    write_checkpoint()
    write_last_any_post("manual")
    refresh_snapshots(settings)
    message_id = response.get("result", {}).get("message_id", "unknown")
    print(f"Опубликовано из файла {path}. Telegram message_id: {message_id}")
    return 0


def command_topic_post(args: argparse.Namespace) -> int:
    settings = get_settings()
    topic = args.topic.strip()
    if not topic:
        raise ConfigError("Тема не может быть пустой.")
    text = generate_post(settings, mode="topic", topic=topic)
    if args.preview:
        print(text)
        print(f"\n---\nСимволов: {len(text)}")
        if args.save:
            path = save_draft(text)
            print(f"Черновик сохранен: {path}")
        return 0

    response = send_to_telegram(settings, text)
    append_history(text, response, kind="topic")
    update_memory(text, kind="topic", topic=topic)
    write_last_any_post("topic")
    message_id = response.get("result", {}).get("message_id", "unknown")
    print(f"Опубликовано (topic). Telegram message_id: {message_id}")
    return 0


def command_memory() -> int:
    print(memory_context())
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m daily_poster",
        description="Generate daily posts with OpenAI and publish them to Telegram.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    preview = subparsers.add_parser("preview", help="Generate a post without publishing.")
    preview.add_argument("--save", action="store_true", help="Save generated post to data/drafts.")
    preview.add_argument("--mode", choices=["activity", "free"], default="activity", help="Post style to preview.")
    preview.set_defaults(func=command_preview)

    publish = subparsers.add_parser("publish", help="Generate and publish a post.")
    publish.set_defaults(func=lambda _args: command_publish())

    maybe_post = subparsers.add_parser("maybe-post", help="Maybe publish an activity or free-form post respecting the minimum pause.")
    maybe_post.add_argument("--force", action="store_true", help="Ignore spontaneous pause/probability checks.")
    maybe_post.add_argument("--mode", choices=["auto", "activity", "free"], default="auto", help="Choose post mode.")
    maybe_post.set_defaults(func=command_maybe_post)

    topic_post = subparsers.add_parser("topic-post", help="Generate or publish a post from your topic/thought.")
    topic_post.add_argument("topic", help="Topic or raw thought to turn into a post.")
    topic_post.add_argument("--preview", action="store_true", help="Print the post instead of publishing.")
    topic_post.add_argument("--save", action="store_true", help="Save preview to data/drafts.")
    topic_post.set_defaults(func=command_topic_post)

    send_file = subparsers.add_parser("send-file", help="Publish text from a local markdown/text file.")
    send_file.add_argument("path", help="Path to the file with post text.")
    send_file.set_defaults(func=command_send_file)

    env_check = subparsers.add_parser("env-check", help="Validate local configuration.")
    env_check.set_defaults(func=lambda _args: command_env_check())

    digest = subparsers.add_parser("digest", help="Show activity score and signal summary since last post.")
    digest.set_defaults(func=lambda _args: command_digest())

    memory = subparsers.add_parser("memory", help="Show local channel memory.")
    memory.set_defaults(func=lambda _args: command_memory())

    doctor = subparsers.add_parser("doctor", help="Run a local configuration and activity health check.")
    doctor.set_defaults(func=lambda _args: command_doctor())

    mark_checkpoint = subparsers.add_parser("mark-checkpoint", help="Mark current files as accounted for without posting.")
    mark_checkpoint.set_defaults(func=lambda _args: command_mark_checkpoint())

    context = subparsers.add_parser("context", help="Show scanned work context since last post without calling OpenAI.")
    context.set_defaults(func=lambda _args: command_context())

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (ConfigError, ApiError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
