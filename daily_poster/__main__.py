from __future__ import annotations

import argparse
import difflib
import hashlib
import html
import json
import os
import platform
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
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"
PROMPT_PATH = PROJECT_ROOT / "config" / "post_prompt.md"
DRAFTS_DIR = PROJECT_ROOT / "data" / "drafts"
HISTORY_PATH = PROJECT_ROOT / "data" / "posts.jsonl"
STATE_PATH = PROJECT_ROOT / "data" / "state.json"
MEMORY_PATH = PROJECT_ROOT / "data" / "memory.json"
GROUP_MEMORY_PATH = PROJECT_ROOT / "data" / "group_memory.json"
SNAPSHOTS_DIR = PROJECT_ROOT / "data" / "snapshots"
LOGS_DIR = PROJECT_ROOT / "logs"
DEBUG_DIR = PROJECT_ROOT / "data" / "debug"

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
TELEGRAM_SEND_MESSAGE_URL = "https://api.telegram.org/bot{token}/sendMessage"
TELEGRAM_GET_UPDATES_URL = "https://api.telegram.org/bot{token}/getUpdates"
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
CHANNEL_BASIS_KINDS = {"imported", "channel"}
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
    active_mode: str
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
    spontaneous_min_pause_minutes: int
    spontaneous_check_interval_minutes: int
    autopilot_enabled: bool
    autopilot_posts_per_day: int
    autopilot_min_pause_minutes: int
    group_chat_enabled: bool
    group_chat_id: str
    group_target_user_id: str
    group_target_username: str
    group_target_name: str
    group_reply_probability: float
    group_min_pause_seconds: int
    group_context_messages: int


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
        spontaneous_min_pause_minutes = int(
            os.environ.get(
                "SPONTANEOUS_MIN_PAUSE_MINUTES",
                str(int(os.environ.get("SPONTANEOUS_MIN_PAUSE_HOURS", "6")) * 60),
            )
        )
        spontaneous_check_interval_minutes = int(
            os.environ.get("SPONTANEOUS_CHECK_INTERVAL_MINUTES", "60")
        )
        autopilot_posts_per_day = int(os.environ.get("AUTOPILOT_POSTS_PER_DAY", "2"))
        autopilot_min_pause_minutes = int(
            os.environ.get(
                "AUTOPILOT_MIN_PAUSE_MINUTES",
                str(int(os.environ.get("AUTOPILOT_MIN_PAUSE_HOURS", "4")) * 60),
            )
        )
        group_min_pause_seconds = int(os.environ.get("GROUP_MIN_PAUSE_SECONDS", "90"))
        group_context_messages = int(os.environ.get("GROUP_CONTEXT_MESSAGES", "16"))
    except ValueError as exc:
        raise ConfigError("ACTIVITY_* и *_MIN_PAUSE_MINUTES должны быть числами.") from exc
    try:
        group_reply_probability = float(os.environ.get("GROUP_REPLY_PROBABILITY", "0.12"))
    except ValueError as exc:
        raise ConfigError("GROUP_REPLY_PROBABILITY должен быть числом от 0 до 1.") from exc
    if not (0 <= group_reply_probability <= 1):
        raise ConfigError("GROUP_REPLY_PROBABILITY должен быть от 0 до 1.")

    scan_roots = parse_paths(os.environ.get("ACTIVITY_SCAN_ROOTS", DEFAULT_SCAN_ROOTS))
    exclude_dirs = DEFAULT_EXCLUDE_DIRS | parse_csv_set(os.environ.get("ACTIVITY_EXCLUDE_DIRS", ""))
    spontaneous_enabled = parse_bool(os.environ.get("SPONTANEOUS_ENABLED", "true"))
    autopilot_enabled = parse_bool(os.environ.get("AUTOPILOT_ENABLED", "false"))
    group_chat_enabled = parse_bool(os.environ.get("GROUP_CHAT_ENABLED", "false"))
    active_mode = normalize_active_mode(os.environ.get("ACTIVE_MODE", "tracking"))

    return Settings(
        active_mode=active_mode,
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
        spontaneous_min_pause_minutes=spontaneous_min_pause_minutes,
        spontaneous_check_interval_minutes=spontaneous_check_interval_minutes,
        autopilot_enabled=autopilot_enabled,
        autopilot_posts_per_day=autopilot_posts_per_day,
        autopilot_min_pause_minutes=autopilot_min_pause_minutes,
        group_chat_enabled=group_chat_enabled,
        group_chat_id=os.environ.get("GROUP_CHAT_ID", ""),
        group_target_user_id=os.environ.get("GROUP_TARGET_USER_ID", ""),
        group_target_username=os.environ.get("GROUP_TARGET_USERNAME", ""),
        group_target_name=os.environ.get("GROUP_TARGET_NAME", ""),
        group_reply_probability=group_reply_probability,
        group_min_pause_seconds=group_min_pause_seconds,
        group_context_messages=group_context_messages,
    )


def parse_csv_set(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}


def parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y", "on", "да"}


def normalize_active_mode(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"autogen", "tracking", "group", "off"}:
        return normalized
    return "tracking"


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


def read_recent_posts(limit: int = 8, preferred_kinds: tuple[str, ...] | None = None) -> list[str]:
    if not HISTORY_PATH.exists():
        return []

    rows = HISTORY_PATH.read_text(encoding="utf-8").splitlines()
    items: list[dict[str, Any]] = []
    for row in rows:
        try:
            item = json.loads(row)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        items.append(item)

    selected: list[dict[str, Any]] = []
    if preferred_kinds:
        preferred = set(preferred_kinds)
        preferred_items = [item for item in items if str(item.get("kind", "")) in preferred]
        if len(preferred_items) >= limit:
            selected = preferred_items[-limit:]
        else:
            remaining = [item for item in items if item not in preferred_items]
            selected = preferred_items + remaining[-max(0, limit - len(preferred_items)):]
    else:
        selected = items

    posts: list[str] = []
    for item in selected[-limit:]:
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            posts.append(text.strip())
    return posts


def history_texts() -> list[str]:
    if not HISTORY_PATH.exists():
        return []
    texts: list[str] = []
    for row in HISTORY_PATH.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(row)
        except json.JSONDecodeError:
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            texts.append(text.strip())
    return texts


def history_items(preferred_kinds: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
    if not HISTORY_PATH.exists():
        return []
    items: list[dict[str, Any]] = []
    for row in HISTORY_PATH.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(row)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        items.append(item)
    if not preferred_kinds:
        return items
    preferred = set(preferred_kinds)
    filtered = [item for item in items if str(item.get("kind", "")) in preferred]
    return filtered or items


def default_memory() -> dict[str, Any]:
    return {
        "topics": {},
        "recent_posts": [],
        "style_profile": {},
        "style_notes": [
            "Писать от первого лица, живо и без упоминания автоматизации.",
            "Не начинать каждый пост одинаково.",
            "База канала задает манеру письма, но не темы для прямого пересказа.",
        ],
    }


def read_memory() -> dict[str, Any]:
    if not MEMORY_PATH.exists():
        return default_memory()
    try:
        memory = json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default_memory()
    return memory if isinstance(memory, dict) else default_memory()


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


def published_history_items() -> list[dict[str, Any]]:
    return [
        item
        for item in history_items()
        if str(item.get("kind", "")) not in CHANNEL_BASIS_KINDS
    ]


def read_recent_published_posts(limit: int = 8) -> list[str]:
    posts: list[str] = []
    for item in published_history_items()[-limit:]:
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            posts.append(text.strip())
    return posts


def top_items(values: list[str], limit: int = 12) -> list[str]:
    counts: dict[str, int] = {}
    for value in values:
        value = value.strip()
        if not value:
            continue
        counts[value] = counts.get(value, 0) + 1
    return [value for value, _count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]]


def sentence_fragments(text: str) -> list[str]:
    fragments = re.split(r"[.!?…\n]+", text)
    return [fragment.strip() for fragment in fragments if 6 <= len(fragment.strip()) <= 80]


def build_style_profile(posts: list[str]) -> dict[str, Any]:
    clean_posts = [post.strip() for post in posts if post.strip()]
    if not clean_posts:
        return {}

    joined = "\n".join(clean_posts)
    lengths = [len(post) for post in clean_posts]
    paragraph_counts = [max(1, len([part for part in post.split("\n\n") if part.strip()])) for post in clean_posts]
    line_counts = [max(1, len([part for part in post.splitlines() if part.strip()])) for post in clean_posts]
    words = re.findall(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9_+-]{2,}", joined)
    slang = [
        word
        for word in words
        if len(word) <= 14 and word.lower() not in {
            "это", "что", "как", "для", "или", "если", "там", "тут", "вот", "уже", "ещё", "еще",
            "the", "and", "for", "you", "this", "that",
        }
    ]
    emoji = re.findall(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", joined)
    hashtags = re.findall(r"#[\wА-Яа-яЁё_]+", joined)
    mentions = re.findall(r"@[A-Za-z0-9_]+", joined)
    punctuation = {
        "...": joined.count("..."),
        "…": joined.count("…"),
        "!": joined.count("!"),
        "?": joined.count("?"),
        "—": joined.count("—"),
        "-": joined.count("-"),
        ":": joined.count(":"),
        ";": joined.count(";"),
    }
    uppercase_words = re.findall(r"\b[A-ZА-ЯЁ]{2,}\b", joined)
    first_person_markers = re.findall(r"\b(?:я|мне|меня|мой|моя|мои|у меня|думаю|кажется)\b", joined.lower())
    openers = top_items([post.splitlines()[0][:80] for post in clean_posts if post.splitlines()], limit=8)
    endings = top_items([post.splitlines()[-1][:80] for post in clean_posts if post.splitlines()], limit=8)
    fragments = top_items([fragment.lower() for post in clean_posts for fragment in sentence_fragments(post)], limit=16)

    return {
        "posts_analyzed": len(clean_posts),
        "avg_chars": round(sum(lengths) / len(lengths)),
        "min_chars": min(lengths),
        "max_chars": max(lengths),
        "avg_paragraphs": round(sum(paragraph_counts) / len(paragraph_counts), 1),
        "avg_lines": round(sum(line_counts) / len(line_counts), 1),
        "keywords": keyword_candidates(joined, limit=28),
        "signature_words": top_items([word.lower() for word in slang], limit=24),
        "emoji": top_items(emoji, limit=16),
        "hashtags": top_items(hashtags, limit=12),
        "mentions": top_items(mentions, limit=12),
        "punctuation": punctuation,
        "uppercase_words": top_items(uppercase_words, limit=12),
        "first_person_density": round(len(first_person_markers) / max(1, len(words)), 3),
        "typical_openers": openers,
        "typical_endings": endings,
        "avoid_fragments": fragments,
    }


def install_channel_style_profile(posts: list[str]) -> None:
    memory = default_memory()
    profile = build_style_profile(posts)
    memory["style_profile"] = profile

    topics = memory.setdefault("topics", {})
    for key in profile.get("keywords", [])[:20]:
        topics[key] = {"count": 1, "last_seen": datetime.now().isoformat(timespec="seconds")}

    write_memory(memory)


def memory_context() -> str:
    memory = read_memory()
    topics = memory.get("topics", {})
    recent_posts = memory.get("recent_posts", [])
    style_notes = memory.get("style_notes", [])
    style_profile = memory.get("style_profile", {})

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
    profile_block = style_profile_context(style_profile if isinstance(style_profile, dict) else {})
    topics_block = "\n".join(topic_lines) if topic_lines else "- пока нет"
    recent_block = "\n".join(recent_lines) if recent_lines else "- пока нет"
    return (
        "Локальная память канала:\n\n"
        f"Стилевые заметки:\n{style_block}\n\n"
        f"{profile_block}\n\n"
        f"Частые темы:\n{topics_block}\n\n"
        f"Последние смысловые следы постов:\n{recent_block}"
    )


def style_profile_context(profile: dict[str, Any]) -> str:
    if not profile:
        return "Профиль стиля: пока нет."

    punctuation = profile.get("punctuation", {})
    if isinstance(punctuation, dict):
        punctuation_block = ", ".join(
            f"{mark}={count}"
            for mark, count in punctuation.items()
            if isinstance(count, int) and count > 0
        ) or "без ярких пунктуационных маркеров"
    else:
        punctuation_block = "без данных"

    def list_field(name: str, limit: int = 14) -> str:
        value = profile.get(name, [])
        if isinstance(value, list):
            return ", ".join(str(item) for item in value[:limit]) or "нет"
        return "нет"

    return textwrap.dedent(
        f"""
        Профиль стиля базы канала:
        - Проанализировано постов: {profile.get("posts_analyzed", 0)}
        - Типичная длина: около {profile.get("avg_chars", "?")} символов; диапазон {profile.get("min_chars", "?")}-{profile.get("max_chars", "?")}
        - Абзацы/строки: {profile.get("avg_paragraphs", "?")} абз., {profile.get("avg_lines", "?")} строк
        - Пунктуация: {punctuation_block}
        - Слова и сленг, которые можно аккуратно использовать: {list_field("signature_words")}
        - Частые тематические поля: {list_field("keywords")}
        - Эмодзи/хэштеги/упоминания: {list_field("emoji", 10)} | {list_field("hashtags", 8)} | {list_field("mentions", 8)}
        - Регистр/капс: {list_field("uppercase_words", 8)}
        - Плотность первого лица: {profile.get("first_person_density", "?")}
        - Не повторять узнаваемые старые фрагменты: {list_field("avoid_fragments", 10)}
        """
    ).strip()


def style_reference_context(preferred_kinds: tuple[str, ...] = ("imported", "channel"), limit: int = 12) -> str:
    memory = read_memory()
    profile = memory.get("style_profile", {})
    own_posts = read_recent_published_posts(limit=limit)
    anti_repeat = "\n".join(f"- {post[:260].replace(chr(10), ' ')}" for post in own_posts[-6:])
    if not profile and not own_posts:
        return "Базы канала пока нет."
    return textwrap.dedent(
        f"""
        База канала нужна только для переноса манеры, а не для пересказа старых тем.

        {style_profile_context(profile if isinstance(profile, dict) else {})}

        Недавние посты, которые уже написал бот. Их нельзя повторять по теме и формулировкам:
        {anti_repeat or "- пока нет"}
        """
    ).strip()


def novelty_seed() -> str:
    seeds = [
        "наблюдение о повседневной детали",
        "короткая личная мысль без вывода морали",
        "реакция на случайную новость или разговор",
        "маленький конфликт ожидания и реальности",
        "заметка про людей, привычки или интернет",
        "ироничная фиксация странного момента дня",
        "спокойная мысль, которую не надо доказывать",
        "резкая, но короткая оценка ситуации",
    ]
    return random.choice(seeds)


def flatten_message_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


def split_plaintext_posts(text: str) -> list[str]:
    blocks = re.split(r"\n\s*\n\s*\n+", text.strip())
    posts = [block.strip() for block in blocks if block.strip()]
    if len(posts) <= 1:
        posts = [block.strip() for block in re.split(r"\n\s*\n", text.strip()) if block.strip()]
    return posts


def extract_posts_from_json(payload: Any) -> list[str]:
    posts: list[str] = []
    if isinstance(payload, dict):
        if isinstance(payload.get("messages"), list):
            for item in payload["messages"]:
                if not isinstance(item, dict):
                    continue
                text = flatten_message_text(item.get("text") or item.get("caption"))
                if text.strip():
                    posts.append(text.strip())
        else:
            text = flatten_message_text(payload.get("text") or payload.get("caption"))
            if text.strip():
                posts.append(text.strip())
    elif isinstance(payload, list):
        for item in payload:
            posts.extend(extract_posts_from_json(item))
    return posts


def extract_group_messages_from_json(payload: Any) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        if isinstance(payload.get("messages"), list):
            for item in payload["messages"]:
                if not isinstance(item, dict):
                    continue
                text = flatten_message_text(item.get("text") or item.get("caption"))
                if not text.strip():
                    continue
                messages.append(
                    {
                        "author_id": str(item.get("from_id") or item.get("actor_id") or "").strip(),
                        "author_name": str(item.get("from") or item.get("actor") or "unknown").strip(),
                        "text": text.strip(),
                    }
                )
        else:
            text = flatten_message_text(payload.get("text") or payload.get("caption"))
            if text.strip():
                messages.append(
                    {
                        "author_id": str(payload.get("from_id") or payload.get("actor_id") or "").strip(),
                        "author_name": str(payload.get("from") or payload.get("actor") or "unknown").strip(),
                        "text": text.strip(),
                    }
                )
    elif isinstance(payload, list):
        for item in payload:
            messages.extend(extract_group_messages_from_json(item))
    return messages


class TelegramHtmlExportParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.posts: list[str] = []
        self._capture_depth = 0
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = {
            part
            for key, value in attrs
            if key == "class" and value
            for part in value.split()
        }
        if "text" in classes:
            if self._capture_depth == 0:
                self._chunks = []
            self._capture_depth += 1
            return
        if self._capture_depth > 0 and tag == "br":
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self._capture_depth <= 0:
            return
        if tag == "div":
            self._capture_depth -= 1
            if self._capture_depth == 0:
                text = html.unescape("".join(self._chunks)).strip()
                text = re.sub(r"\n{3,}", "\n\n", text)
                if text:
                    self.posts.append(text)
                self._chunks = []

    def handle_data(self, data: str) -> None:
        if self._capture_depth > 0:
            self._chunks.append(data)


def extract_posts_from_html(text: str) -> list[str]:
    parser = TelegramHtmlExportParser()
    parser.feed(text)
    parser.close()
    return parser.posts


class TelegramGroupHtmlExportParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.messages: list[dict[str, Any]] = []
        self._message_depth = 0
        self._capture: str | None = None
        self._capture_depth = 0
        self._author_chunks: list[str] = []
        self._text_chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = {
            part
            for key, value in attrs
            if key == "class" and value
            for part in value.split()
        }
        if tag == "div" and "message" in classes:
            if self._message_depth == 0:
                self._author_chunks = []
                self._text_chunks = []
            self._message_depth += 1
            return
        if self._message_depth <= 0:
            return
        if tag == "div":
            self._message_depth += 1
            if "from_name" in classes:
                self._capture = "author"
                self._capture_depth = 1
            elif "text" in classes:
                self._capture = "text"
                self._capture_depth = 1
        elif self._capture == "text" and tag == "br":
            self._text_chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self._message_depth <= 0 or tag != "div":
            return
        if self._capture:
            self._capture_depth -= 1
            if self._capture_depth <= 0:
                self._capture = None
        self._message_depth -= 1
        if self._message_depth == 0:
            author = html.unescape("".join(self._author_chunks)).strip()
            text = html.unescape("".join(self._text_chunks)).strip()
            text = re.sub(r"\n{3,}", "\n\n", text)
            if text:
                self.messages.append({"author_id": "", "author_name": author or "unknown", "text": text})

    def handle_data(self, data: str) -> None:
        if self._capture == "author":
            self._author_chunks.append(data)
        elif self._capture == "text":
            self._text_chunks.append(data)


def extract_group_messages_from_html(text: str) -> list[dict[str, Any]]:
    parser = TelegramGroupHtmlExportParser()
    parser.feed(text)
    parser.close()
    return parser.messages


def iter_import_sources(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if not path.is_dir():
        raise ConfigError(f"Путь для импорта не найден: {path}")
    supported = {".txt", ".md", ".json", ".jsonl", ".html", ".htm"}
    files = [item for item in sorted(path.rglob("*")) if item.is_file() and item.suffix.lower() in supported]
    if not files:
        raise ConfigError(f"В папке не найдено поддерживаемых файлов для импорта: {path}")
    return files


def load_posts_for_import(path: Path) -> list[str]:
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8", errors="replace")
    if suffix == ".jsonl":
        posts: list[str] = []
        for row in text.splitlines():
            row = row.strip()
            if not row:
                continue
            try:
                payload = json.loads(row)
            except json.JSONDecodeError:
                continue
            posts.extend(extract_posts_from_json(payload))
        return posts
    if suffix == ".json":
        payload = json.loads(text)
        return extract_posts_from_json(payload)
    if suffix in {".html", ".htm"}:
        return extract_posts_from_html(text)
    return split_plaintext_posts(text)


def load_group_messages_for_import(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8", errors="replace")
    if suffix == ".jsonl":
        messages: list[dict[str, Any]] = []
        for row in text.splitlines():
            row = row.strip()
            if not row:
                continue
            try:
                payload = json.loads(row)
            except json.JSONDecodeError:
                continue
            messages.extend(extract_group_messages_from_json(payload))
        return messages
    if suffix == ".json":
        payload = json.loads(text)
        return extract_group_messages_from_json(payload)
    if suffix in {".html", ".htm"}:
        return extract_group_messages_from_html(text)
    messages = []
    for block in split_plaintext_posts(text):
        if ":" in block:
            author, message = block.split(":", 1)
            messages.append({"author_id": "", "author_name": author.strip() or "unknown", "text": message.strip()})
    return messages


def rewrite_history_excluding_kinds(kinds: set[str]) -> int:
    if not HISTORY_PATH.exists():
        return 0
    kept: list[str] = []
    removed = 0
    for row in HISTORY_PATH.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(row)
        except json.JSONDecodeError:
            kept.append(row)
            continue
        if isinstance(item, dict) and str(item.get("kind", "")) in kinds:
            removed += 1
            continue
        kept.append(row)
    ensure_dirs()
    HISTORY_PATH.write_text(("\n".join(kept) + "\n") if kept else "", encoding="utf-8")
    return removed


def reset_channel_basis() -> int:
    removed = rewrite_history_excluding_kinds(CHANNEL_BASIS_KINDS)
    write_memory(default_memory())
    return removed


def import_channel_history(path: Path, replace: bool = True) -> tuple[int, int]:
    sources = iter_import_sources(path)
    if replace:
        reset_channel_basis()
    existing: set[str] = set()
    imported_posts: list[str] = []
    imported = 0
    skipped = 0
    for source in sources:
        try:
            posts = load_posts_for_import(source)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"Не удалось разобрать JSON-файл: {source}") from exc
        for text in posts:
            normalized = text.strip()
            if not normalized:
                continue
            if normalized in existing:
                skipped += 1
                continue
            append_history(normalized, kind="imported")
            existing.add(normalized)
            imported_posts.append(normalized)
            imported += 1
    install_channel_style_profile(imported_posts)
    return imported, skipped


def make_generation_input(
    settings: Settings,
    editorial_prompt: str,
    recent_posts: list[str],
    activity_context: str,
    mode: str = "activity",
) -> str:
    recent_block = "\n\n---\n\n".join(recent_posts) if recent_posts else "Истории пока нет."
    memory_block = memory_context()
    style_block = style_reference_context()
    seed = novelty_seed()
    today = datetime.now().strftime("%Y-%m-%d")
    if mode == "free":
        return textwrap.dedent(
            f"""
            Сегодня: {today}
            Язык поста: {settings.post_language}
            Максимальная длина: {settings.post_max_chars} символов.

            {style_block}

            Режим: свободный авторский пост.
            Новый смысловой импульс: {seed}.
            Скопируй манеру: длину, ритм, пунктуацию, регистр, сленг, степень резкости, способ начинать и заканчивать мысль.
            Не копируй содержание базы канала. Старые посты - только материал для анализа голоса.
            Не развивай снова конкретные сюжеты, имена, покупки, события и выводы из базы, если они не появились в новом импульсе.
            Не уходи в программирование, Scala, AI, рабочие заметки или инженерные темы, если это не естественная часть стиля канала.
            Напиши короткий живой пост от первого лица, будто мысль сама дозрела и захотелось ей поделиться.
            Возьми один новый мотив, одну новую реакцию или один новый тип шутки. Не склеивай несколько старых постов в один.
            Не выдумывай конкретную сегодняшнюю работу, баги, тесты, задачи или файлы.
            Не упоминай файлы, сканирование, бота, модель, автоматизацию, отчетность или то, что пост был сгенерирован.
            Нельзя использовать узнаваемые фразы или последовательности идей из базы. Если фраза выглядит как цитата из старого поста - перепридумай ее.
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
    if mode == "autopilot":
        return textwrap.dedent(
            f"""
            Сегодня: {today}
            Язык поста: {settings.post_language}
            Максимальная длина: {settings.post_max_chars} символов.

            {style_block}

            Режим: автоведение канала.
            Новый смысловой импульс: {seed}.
            Пиши так, будто это естественное продолжение уже существующего канала.
            Главная задача - перенести манеру: тон, ритм, тип подачи, длину абзацев, словарь, пунктуацию, регистр, сленг и общий вайб.
            Вторая задача - придумать новый самостоятельный повод для поста, а не пересобрать старые посты по кускам.
            Если в базе канала нет кода, разработки, AI или учебных заметок - не тащи эти темы в новый пост.
            Выбери одну новую тему, один сюжет или один эмоциональный угол. Не делай подборку и не склеивай несколько разных постов в один.
            Пост должен выглядеть так, будто автор сам решил написать мысль, а не будто система выполняет расписание.
            Не упоминай автоведение, память, историю, бота, модель, файлы, автоматизацию или то, что ты имитируешь стиль.
            Нельзя повторять имена, события, покупки, выводы, шутки и связки из базы, если это не неизбежная часть стиля.
            Нельзя использовать узнаваемые старые фрагменты; продолжай линию канала новым, максимально органичным постом.
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


def minutes_since_last_post() -> float:
    last = read_last_any_post_time()
    if last <= 0:
        return 10_000_000.0
    return max(0.0, (datetime.now().timestamp() - last) / 60)


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


def posts_stats() -> dict[str, Any]:
    stats = {"today": 0, "total": 0, "by_kind": {}}
    today = date.today().isoformat()
    if not HISTORY_PATH.exists():
        return stats
    for row in HISTORY_PATH.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(row)
        except json.JSONDecodeError:
            continue
        kind = str(item.get("kind", "unknown"))
        if kind in CHANNEL_BASIS_KINDS:
            continue
        stats["total"] += 1
        stats["by_kind"][kind] = stats["by_kind"].get(kind, 0) + 1
        created_at = str(item.get("created_at", ""))
        if created_at.startswith(today):
            stats["today"] += 1
    return stats


def mode_summary(settings: Settings, mode: str) -> str:
    posts = posts_stats()
    if mode == "autogen":
        return textwrap.dedent(
            f"""
            Режим: Автогенерация
            Активен: {"да" if settings.active_mode == "autogen" else "нет"}
            Автоведение включено: {"да" if settings.autopilot_enabled else "нет"}
            Минимальная пауза: {settings.autopilot_min_pause_minutes} мин
            Лимит автопостов в день: {settings.autopilot_posts_per_day}
            Постов сегодня: {posts["today"]}
            Постов всего: {posts["total"]}
            С последнего поста прошло: {minutes_since_last_post():.1f} мин
            """
        ).strip()
    if mode == "tracking":
        stats = activity_stats(settings)
        return textwrap.dedent(
            f"""
            Режим: Отслеживание
            Активен: {"да" if settings.active_mode == "tracking" else "нет"}
            Папки: {", ".join(str(path) for path in settings.activity_scan_roots)}
            Измененных файлов с checkpoint: {stats["files"]}
            Сильных сигналов: {stats["strong_files"]}
            Слабых сигналов: {stats["weak_files"]}
            Score: {stats["score"]}/100
            Checkpoint: {checkpoint_label()}
            Постов сегодня: {posts["today"]}
            Постов всего: {posts["total"]}
            """
        ).strip()
    return "Режим выключен."


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


def get_json(url: str, params: dict[str, Any]) -> dict[str, Any]:
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(f"{url}?{query}", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise ApiError(f"HTTP {exc.code}: {details}") from exc
    except urllib.error.URLError as exc:
        raise ApiError(f"Ошибка сети: {exc.reason}") from exc

    try:
        return json.loads(data)
    except json.JSONDecodeError as exc:
        raise ApiError(f"API вернул не JSON: {data[:500]}") from exc


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
    if mode in {"free", "autopilot"}:
        recent_posts = read_recent_published_posts(limit=10)
    else:
        recent_posts = read_recent_posts()
    if mode == "activity":
        activity_context = build_activity_context(settings)
    elif mode == "topic":
        activity_context = topic or ""
    elif mode in {"free", "autopilot"}:
        activity_context = "Свободный пост без файлового контекста."
    else:
        activity_context = "Свободный пост без файлового контекста."
    prompt = make_generation_input(settings, editorial_prompt, recent_posts, activity_context, mode=mode)
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


def send_group_reply(settings: Settings, text: str, chat_id: str | int, reply_to_message_id: int | None = None) -> dict[str, Any]:
    validate_post(text, min(settings.post_max_chars, 1200))
    url = TELEGRAM_SEND_MESSAGE_URL.format(token=urllib.parse.quote(settings.telegram_bot_token))
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
        "allow_sending_without_reply": True,
    }
    if reply_to_message_id is not None:
        payload["reply_to_message_id"] = reply_to_message_id
    response = post_json(url, payload)
    if not response.get("ok"):
        raise ApiError(f"Telegram API вернул ошибку: {json.dumps(response, ensure_ascii=False)}")
    return response


def telegram_chat_matches(chat: dict[str, Any], settings: Settings) -> bool:
    chat_id = settings.telegram_chat_id
    if str(chat.get("id")) == chat_id:
        return True
    username = chat.get("username")
    return isinstance(username, str) and chat_id.lower() == f"@{username.lower()}"


def telegram_group_chat_matches(chat: dict[str, Any], settings: Settings) -> bool:
    chat_id = settings.group_chat_id or settings.telegram_chat_id
    if not chat_id:
        return False
    if str(chat.get("id")) == chat_id:
        return True
    username = chat.get("username")
    return isinstance(username, str) and chat_id.lower() == f"@{username.lower()}"


def telegram_user_name(user: dict[str, Any]) -> str:
    username = user.get("username")
    if isinstance(username, str) and username:
        return f"@{username}"
    first = str(user.get("first_name") or "").strip()
    last = str(user.get("last_name") or "").strip()
    return " ".join(part for part in (first, last) if part) or str(user.get("id", "unknown"))


def telegram_user_matches(user: dict[str, Any], settings: Settings) -> bool:
    target_id = settings.group_target_user_id.strip()
    if target_id and str(user.get("id")) == target_id:
        return True
    target_username = settings.group_target_username.strip().lstrip("@").lower()
    username = str(user.get("username") or "").lower()
    if target_username and username == target_username:
        return True
    target_name = settings.group_target_name.strip().lower()
    full_name = telegram_user_name(user).lstrip("@").lower()
    return bool(target_name and full_name == target_name)


def message_text(message: dict[str, Any]) -> str:
    text = message.get("text") or message.get("caption")
    return text.strip() if isinstance(text, str) else ""


def default_group_memory() -> dict[str, Any]:
    return {
        "archive_messages": [],
        "participants": [],
        "target_participant": {},
        "target_messages": [],
        "recent_messages": [],
        "bot_replies": [],
        "style_profile": {},
    }


def read_group_memory() -> dict[str, Any]:
    if not GROUP_MEMORY_PATH.exists():
        return default_group_memory()
    try:
        memory = json.loads(GROUP_MEMORY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default_group_memory()
    return memory if isinstance(memory, dict) else default_group_memory()


def write_group_memory(memory: dict[str, Any]) -> None:
    ensure_dirs()
    GROUP_MEMORY_PATH.write_text(json.dumps(memory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def participant_key(message: dict[str, Any]) -> str:
    author_id = str(message.get("author_id") or "").strip()
    if author_id:
        return author_id
    return str(message.get("author_name") or "unknown").strip().lower()


def group_archive_participants(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    for message in messages:
        key = participant_key(message)
        item = by_key.setdefault(
            key,
            {
                "key": key,
                "author_id": str(message.get("author_id") or "").strip(),
                "author_name": str(message.get("author_name") or "unknown").strip(),
                "count": 0,
            },
        )
        item["count"] = int(item.get("count", 0)) + 1
        if not item.get("author_name") or item.get("author_name") == "unknown":
            item["author_name"] = str(message.get("author_name") or "unknown").strip()
    return sorted(by_key.values(), key=lambda item: (-int(item.get("count", 0)), str(item.get("author_name", ""))))


def import_group_archive(path: Path, replace: bool = True) -> tuple[int, list[dict[str, Any]]]:
    sources = iter_import_sources(path)
    imported_messages: list[dict[str, Any]] = []
    for source in sources:
        try:
            imported_messages.extend(load_group_messages_for_import(source))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"Не удалось разобрать JSON-файл: {source}") from exc

    cleaned = [
        {
            "author_id": str(item.get("author_id") or "").strip(),
            "author_name": str(item.get("author_name") or "unknown").strip(),
            "text": str(item.get("text") or "").strip()[:1000],
        }
        for item in imported_messages
        if str(item.get("text") or "").strip()
    ]
    participants = group_archive_participants(cleaned)

    memory = default_group_memory() if replace else read_group_memory()
    existing = memory.get("archive_messages", [])
    if not isinstance(existing, list) or replace:
        existing = []
    existing.extend(cleaned)
    memory["archive_messages"] = existing[-5000:]
    memory["participants"] = group_archive_participants(memory["archive_messages"])
    if replace:
        memory["target_participant"] = {}
        memory["target_messages"] = []
        memory["style_profile"] = {}
    write_group_memory(memory)
    return len(cleaned), participants


def select_group_archive_participant(selector: str) -> dict[str, Any]:
    selector = selector.strip()
    if not selector:
        raise ConfigError("Не указан участник архива.")
    memory = read_group_memory()
    messages = memory.get("archive_messages", [])
    participants = memory.get("participants", [])
    if not isinstance(messages, list) or not messages:
        raise ConfigError("Архив группового чата еще не загружен.")
    if not isinstance(participants, list):
        participants = group_archive_participants(messages)

    selected: dict[str, Any] | None = None
    if selector.isdigit():
        idx = int(selector) - 1
        if 0 <= idx < len(participants) and isinstance(participants[idx], dict):
            selected = participants[idx]
    if selected is None:
        normalized = selector.strip().lstrip("@").lower()
        for participant in participants:
            if not isinstance(participant, dict):
                continue
            candidates = {
                str(participant.get("key", "")).lstrip("@").lower(),
                str(participant.get("author_id", "")).lstrip("@").lower(),
                str(participant.get("author_name", "")).lstrip("@").lower(),
            }
            if normalized in candidates:
                selected = participant
                break
    if selected is None:
        raise ConfigError(f"Участник не найден в архиве: {selector}")

    selected_key = str(selected.get("key") or "")
    target_messages = [
        item
        for item in messages
        if isinstance(item, dict) and participant_key(item) == selected_key
    ]
    posts = [str(item.get("text", "")) for item in target_messages]
    memory["target_participant"] = selected
    memory["target_messages"] = target_messages[-300:]
    memory["style_profile"] = build_style_profile(posts)
    write_group_memory(memory)
    return selected


def remember_group_message(user: dict[str, Any], text: str, is_target: bool) -> None:
    memory = read_group_memory()
    recent = memory.setdefault("recent_messages", [])
    record = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "user_id": user.get("id"),
        "user": telegram_user_name(user),
        "text": text[:1000],
    }
    if isinstance(recent, list):
        recent.append(record)
        memory["recent_messages"] = recent[-120:]

    if is_target:
        target_messages = memory.setdefault("target_messages", [])
        if isinstance(target_messages, list):
            target_messages.append(record)
            memory["target_messages"] = target_messages[-300:]
            posts = [str(item.get("text", "")) for item in memory["target_messages"] if isinstance(item, dict)]
            memory["style_profile"] = build_style_profile(posts)

    write_group_memory(memory)


def remember_group_reply(text: str) -> None:
    memory = read_group_memory()
    replies = memory.setdefault("bot_replies", [])
    if isinstance(replies, list):
        replies.append({"created_at": datetime.now().isoformat(timespec="seconds"), "text": text[:1000]})
        memory["bot_replies"] = replies[-80:]
    write_group_memory(memory)


def group_memory_context(settings: Settings) -> str:
    memory = read_group_memory()
    profile = memory.get("style_profile", {})
    recent = memory.get("recent_messages", [])
    replies = memory.get("bot_replies", [])
    target_participant = memory.get("target_participant", {})
    if isinstance(target_participant, dict) and target_participant:
        target_line = (
            f"{target_participant.get('author_name', 'unknown')} "
            f"({target_participant.get('count', 0)} сообщений в архиве)"
        )
    else:
        target_line = "не выбран"

    recent_lines: list[str] = []
    if isinstance(recent, list):
        for item in recent[-settings.group_context_messages:]:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text", "")).replace("\n", " ")[:220]
            recent_lines.append(f"- {item.get('user', 'unknown')}: {text}")

    reply_lines: list[str] = []
    if isinstance(replies, list):
        for item in replies[-6:]:
            if isinstance(item, dict):
                reply_lines.append(f"- {str(item.get('text', '')).replace(chr(10), ' ')[:220]}")

    return textwrap.dedent(
        f"""
        Выбранный участник архива: {target_line}

        Профиль человека, которого нужно имитировать:
        {style_profile_context(profile if isinstance(profile, dict) else {})}

        Последний контекст группового чата:
        {chr(10).join(recent_lines) or "- пока нет"}

        Последние ответы бота, чтобы не повторяться:
        {chr(10).join(reply_lines) or "- пока нет"}
        """
    ).strip()


def generate_group_reply(settings: Settings, incoming_text: str, incoming_user: str) -> str:
    context = group_memory_context(settings)
    prompt = textwrap.dedent(
        f"""
        Язык ответа: {settings.post_language}
        Максимальная длина: {min(settings.post_max_chars, 1200)} символов.

        {context}

        Новое сообщение в группе от {incoming_user}:
        {incoming_text}

        Задача: ответь коротко в групповом чате так, будто пишет человек из профиля выше.
        Копируй стиль: пунктуацию, регистр, сленг, длину фраз, резкость/мягкость, привычные слова.
        Не копируй старые сообщения дословно и не пересобирай их кусками.
        Не объясняй, что ты бот или имитируешь человека.
        Не отвечай слишком полно: это живой чат, не пост в канал.
        Если уместно, можно ответить одной фразой или шуткой.
        Верни только текст сообщения.
        """
    ).strip()
    payload = {
        "model": settings.openai_model,
        "instructions": "Ты пишешь короткие реплики для группового Telegram-чата в заданной манере.",
        "input": prompt,
        "max_output_tokens": 700,
    }
    if supports_temperature(settings.openai_model):
        payload["temperature"] = settings.post_temperature
    response = post_json(
        OPENAI_RESPONSES_URL,
        payload,
        headers={"Authorization": f"Bearer {settings.openai_api_key}"},
    )
    text = extract_response_text(response)
    validate_post(text, min(settings.post_max_chars, 1200))
    return text


def seconds_since_group_reply() -> float:
    state = read_state()
    raw = state.get("group_last_reply_at")
    if not isinstance(raw, str) or not raw:
        return 1_000_000.0
    try:
        last = datetime.fromisoformat(raw)
    except ValueError:
        return 1_000_000.0
    return max(0.0, (datetime.now() - last).total_seconds())


def write_last_group_reply() -> None:
    state = read_state()
    state["group_last_reply_at"] = datetime.now().isoformat(timespec="seconds")
    write_state(state)


def sync_channel_memory(settings: Settings) -> int:
    state = read_state()
    offset = int(state.get("telegram_update_offset", 0) or 0)
    url = TELEGRAM_GET_UPDATES_URL.format(token=urllib.parse.quote(settings.telegram_bot_token))
    response = get_json(
        url,
        {
            "offset": offset,
            "timeout": 0,
            "allowed_updates": json.dumps(["channel_post", "edited_channel_post"]),
        },
    )
    if not response.get("ok"):
        raise ApiError(f"Telegram getUpdates вернул ошибку: {json.dumps(response, ensure_ascii=False)}")

    imported = 0
    max_update_id = offset - 1
    for update in response.get("result", []):
        if not isinstance(update, dict):
            continue
        update_id = update.get("update_id")
        if isinstance(update_id, int):
            max_update_id = max(max_update_id, update_id)
        post = update.get("channel_post") or update.get("edited_channel_post")
        if not isinstance(post, dict):
            continue
        chat = post.get("chat")
        if not isinstance(chat, dict) or not telegram_chat_matches(chat, settings):
            continue
        text = post.get("text") or post.get("caption")
        if not isinstance(text, str) or not text.strip():
            continue
        update_memory(text.strip(), kind="channel")
        imported += 1

    if max_update_id >= offset:
        state["telegram_update_offset"] = max_update_id + 1
        write_state(state)
    return imported


def autopilot_posts_today() -> int:
    today = date.today().isoformat()
    count = 0
    if not HISTORY_PATH.exists():
        return 0
    for row in HISTORY_PATH.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(row)
        except json.JSONDecodeError:
            continue
        if item.get("kind") != "autopilot":
            continue
        created_at = str(item.get("created_at", ""))
        if created_at.startswith(today):
            count += 1
    return count


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
    print(f"Active mode: {settings.active_mode}")
    print(f"OpenAI model: {settings.openai_model}")
    print(f"Telegram chat: {settings.telegram_chat_id}")
    print(f"Post max chars: {settings.post_max_chars}")
    print("Activity scan roots:")
    for root in settings.activity_scan_roots:
        print(f"- {root}")
    print(f"Activity max files: {settings.activity_max_files}")
    print(f"Last post checkpoint: {checkpoint_label()}")
    print(f"Spontaneous enabled: {settings.spontaneous_enabled}")
    print(f"Spontaneous min pause minutes: {settings.spontaneous_min_pause_minutes}")
    print(f"Spontaneous check interval minutes: {settings.spontaneous_check_interval_minutes}")
    print(f"Minutes since last post: {minutes_since_last_post():.1f}")
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

    if platform.system() == "Darwin":
        launch_agent = Path.home() / "Library" / "LaunchAgents" / "com.codex.daily-poster.plist"
        if not launch_agent.exists():
            warnings.append(f"LaunchAgent is not installed: {launch_agent}")

    print("tgauto doctor")
    print(f"Project: {PROJECT_ROOT}")
    print(f"Platform: {platform.system()}")
    print(f"Active mode: {settings.active_mode}")
    print(f"Model: {settings.openai_model}")
    print(f"Channel: {settings.telegram_chat_id}")
    print(f"Checkpoint: {checkpoint_label()}")
    print(f"Last post age: {minutes_since_last_post():.1f} min")
    print(
        f"Spontaneous: {settings.spontaneous_enabled}, min pause {settings.spontaneous_min_pause_minutes} min, "
        f"check interval {settings.spontaneous_check_interval_minutes} min"
    )
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
    if settings.active_mode == "autogen":
        return "autopilot"
    stats = activity_stats(settings)
    if int(stats["strong_files"]) > 0 and int(stats["score"]) >= 18:
        return "activity"
    return "free"


def command_maybe_post(args: argparse.Namespace) -> int:
    settings = get_settings()
    if settings.active_mode != "autogen" and not args.force:
        print("Skipped: активен не режим автогенерации (ACTIVE_MODE != autogen).")
        return 0
    if not settings.spontaneous_enabled and not args.force:
        print("Skipped: spontaneous posting is disabled.")
        return 0

    elapsed = minutes_since_last_post()
    if elapsed < settings.spontaneous_min_pause_minutes and not args.force:
        print(
            f"Skipped: last post was {elapsed:.1f} min ago; "
            f"minimum pause is {settings.spontaneous_min_pause_minutes} min."
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
    if settings.active_mode != "tracking":
        print("Skipped: публикация по контексту доступна только в режиме отслеживания (ACTIVE_MODE=tracking).")
        return 0
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


def command_sync_channel() -> int:
    settings = get_settings()
    imported = sync_channel_memory(settings)
    print(f"Импортировано новых постов из Telegram updates: {imported}")
    return 0


def command_group_chat(args: argparse.Namespace) -> int:
    settings = get_settings()
    if settings.active_mode != "group" and not args.force:
        print("Skipped: активен не режим группового чата (ACTIVE_MODE != group).")
        return 0
    if not settings.group_chat_enabled and not args.force:
        print("Skipped: групповой чат выключен (GROUP_CHAT_ENABLED=false).")
        return 0
    if not (settings.group_target_user_id or settings.group_target_username or settings.group_target_name):
        raise ConfigError("Нужно указать GROUP_TARGET_USER_ID, GROUP_TARGET_USERNAME или GROUP_TARGET_NAME.")
    if not (settings.group_chat_id or settings.telegram_chat_id):
        raise ConfigError("Нужно указать GROUP_CHAT_ID или TELEGRAM_CHAT_ID.")

    state = read_state()
    offset = int(state.get("group_update_offset", 0) or 0)
    url = TELEGRAM_GET_UPDATES_URL.format(token=urllib.parse.quote(settings.telegram_bot_token))
    response = get_json(
        url,
        {
            "offset": offset,
            "timeout": 0,
            "allowed_updates": json.dumps(["message"]),
        },
    )
    if not response.get("ok"):
        raise ApiError(f"Telegram getUpdates вернул ошибку: {json.dumps(response, ensure_ascii=False)}")

    learned = 0
    seen = 0
    replied = 0
    skipped_by_chance = 0
    max_update_id = offset - 1
    for update in response.get("result", []):
        if not isinstance(update, dict):
            continue
        update_id = update.get("update_id")
        if isinstance(update_id, int):
            max_update_id = max(max_update_id, update_id)
        message = update.get("message")
        if not isinstance(message, dict):
            continue
        chat = message.get("chat")
        if not isinstance(chat, dict) or not telegram_group_chat_matches(chat, settings):
            continue
        user = message.get("from")
        if not isinstance(user, dict) or user.get("is_bot"):
            continue
        text = message_text(message)
        if not text or text.startswith("/"):
            continue

        seen += 1
        is_target = telegram_user_matches(user, settings)
        remember_group_message(user, text, is_target=is_target)
        if is_target:
            learned += 1
            continue

        if seconds_since_group_reply() < settings.group_min_pause_seconds and not args.force:
            continue
        if random.random() > settings.group_reply_probability and not args.force:
            skipped_by_chance += 1
            continue

        reply = generate_group_reply(settings, incoming_text=text, incoming_user=telegram_user_name(user))
        send_group_reply(settings, reply, chat.get("id"), message.get("message_id"))
        remember_group_reply(reply)
        write_last_group_reply()
        replied += 1
        if not args.reply_all:
            break

    if max_update_id >= offset:
        state = read_state()
        state["group_update_offset"] = max_update_id + 1
        write_state(state)

    print(f"Group chat: seen={seen}, learned={learned}, replied={replied}, skipped_by_chance={skipped_by_chance}")
    return 0


def command_group_import_archive(args: argparse.Namespace) -> int:
    path = Path(args.path).expanduser().resolve()
    imported, participants = import_group_archive(path, replace=True)
    print(f"Загружено сообщений группового архива: {imported}")
    print("Участники:")
    for idx, participant in enumerate(participants, start=1):
        name = participant.get("author_name", "unknown")
        author_id = participant.get("author_id") or participant.get("key") or ""
        count = participant.get("count", 0)
        print(f"{idx}. {name} | {author_id} | {count} сообщений")
    return 0


def command_group_use_participant(args: argparse.Namespace) -> int:
    participant = select_group_archive_participant(args.selector)
    print(
        "Выбран участник: "
        f"{participant.get('author_name', 'unknown')} "
        f"({participant.get('count', 0)} сообщений)"
    )
    return 0


def command_import_history(args: argparse.Namespace) -> int:
    path = Path(args.path).expanduser().resolve()
    imported, skipped = import_channel_history(path)
    print("База канала обновлена.")
    print(f"Загружено постов для стиля: {imported}")
    print(f"Пропущено дублей внутри импорта: {skipped}")
    return 0


def command_autopilot(args: argparse.Namespace) -> int:
    settings = get_settings()
    if settings.active_mode != "autogen" and not args.force:
        print("Skipped: активен не режим автогенерации (ACTIVE_MODE != autogen).")
        return 0
    imported = sync_channel_memory(settings)
    if imported:
        print(f"Синхронизировал память канала: +{imported} постов.")

    if not settings.autopilot_enabled and not args.force:
        print("Skipped: автоведение выключено (AUTOPILOT_ENABLED=false).")
        return 0

    elapsed = minutes_since_last_post()
    min_pause = settings.autopilot_min_pause_minutes
    if elapsed < min_pause and not args.force:
        print(f"Skipped: последний пост был {elapsed:.1f} мин назад; пауза автоведения {min_pause} мин.")
        return 0

    today_count = autopilot_posts_today()
    if today_count >= settings.autopilot_posts_per_day and not args.force:
        print(f"Skipped: лимит автоведения на сегодня исчерпан ({today_count}/{settings.autopilot_posts_per_day}).")
        return 0

    text = generate_post(settings, mode="autopilot")
    if args.preview:
        print(text)
        print(f"\n---\nСимволов: {len(text)}")
        if args.save:
            path = save_draft(text)
            print(f"Черновик сохранен: {path}")
        return 0

    response = send_to_telegram(settings, text)
    append_history(text, response, kind="autopilot")
    update_memory(text, kind="autopilot")
    write_last_any_post("autopilot")
    message_id = response.get("result", {}).get("message_id", "unknown")
    print(f"Опубликовано (autopilot). Telegram message_id: {message_id}")
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

    autopilot = subparsers.add_parser("autopilot", help="Run channel autopilot once, respecting configured limits.")
    autopilot.add_argument("--force", action="store_true", help="Ignore enabled/pause/daily-limit checks.")
    autopilot.add_argument("--preview", action="store_true", help="Generate autopilot post without publishing.")
    autopilot.add_argument("--save", action="store_true", help="Save preview to data/drafts.")
    autopilot.set_defaults(func=command_autopilot)

    send_file = subparsers.add_parser("send-file", help="Publish text from a local markdown/text file.")
    send_file.add_argument("path", help="Path to the file with post text.")
    send_file.set_defaults(func=command_send_file)

    env_check = subparsers.add_parser("env-check", help="Validate local configuration.")
    env_check.set_defaults(func=lambda _args: command_env_check())

    digest = subparsers.add_parser("digest", help="Show activity score and signal summary since last post.")
    digest.set_defaults(func=lambda _args: command_digest())

    memory = subparsers.add_parser("memory", help="Show local channel memory.")
    memory.set_defaults(func=lambda _args: command_memory())

    sync_channel = subparsers.add_parser("sync-channel", help="Import new channel posts from Telegram updates into memory.")
    sync_channel.set_defaults(func=lambda _args: command_sync_channel())

    group_chat = subparsers.add_parser("group-chat", help="Poll a Telegram group and sometimes reply in a target user's style.")
    group_chat.add_argument("--force", action="store_true", help="Ignore active mode, enabled flag, pause and probability.")
    group_chat.add_argument("--reply-all", action="store_true", help="Reply to every eligible message in this poll instead of stopping after one.")
    group_chat.set_defaults(func=command_group_chat)

    group_import = subparsers.add_parser("group-import-archive", help="Import a Telegram group chat export and list participants.")
    group_import.add_argument("path", help="Path to a Telegram group export file or folder.")
    group_import.set_defaults(func=command_group_import_archive)

    group_use = subparsers.add_parser("group-use-participant", help="Choose a participant from the imported group archive.")
    group_use.add_argument("selector", help="Participant number, id or exact name from group-import-archive output.")
    group_use.set_defaults(func=command_group_use_participant)

    import_history = subparsers.add_parser("import-history", help="Replace the channel style basis from a Telegram export.")
    import_history.add_argument("path", help="Path to a .txt, .md, .json, .jsonl, .html/.htm file or a folder with exports.")
    import_history.set_defaults(func=command_import_history)

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
