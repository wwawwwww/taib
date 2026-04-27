# TG Auto

`TG Auto` - это локальный AI-бот для ведения Telegram-канала.

Он умеет работать в двух основных режимах:

- `Автогенерация` - пишет свободные посты в стилистике канала, опираясь на память и историю.
- `Отслеживание` - смотрит изменения в выбранных папках и превращает рабочий контекст в посты от первого лица.

Внутри есть TUI-интерфейс `tgauto`, локальная память канала, импорт старых постов, ручной пост по теме и CLI-команды для автоматизации.

## Что нужно заранее

Для любой системы понадобится:

1. OpenAI API key
2. Telegram-бот от `@BotFather`
3. Telegram-канал
4. Бот-админ в этом канале
5. `git`
6. Python `3.10+`

Минимальный `.env` после настройки выглядит так:

```env
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-5-mini
TELEGRAM_BOT_TOKEN=123456789:ABC...
TELEGRAM_CHAT_ID=@your_channel
ACTIVE_MODE=autogen
```

## Как устроен интерфейс

После запуска `tgauto` ты увидишь четыре раздела:

1. `Автогенерация`
2. `Отслеживание`
3. `Пост по теме`
4. `Настройки`

Важно: `Автогенерация` и `Отслеживание` взаимоисключающие. Активным может быть только один режим.

## macOS

### Установка

```bash
git clone <URL_РЕПОЗИТОРИЯ>
cd <ИМЯ_ПАПКИ_РЕПО>
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
cp .env.example .env
```

Открой `.env` и заполни:

```env
OPENAI_API_KEY=sk-...
TELEGRAM_BOT_TOKEN=123456789:ABC...
TELEGRAM_CHAT_ID=@your_channel
ACTIVE_MODE=autogen
```

### Первый автогенерированный пост

1. Запусти:

```bash
tgauto
```

2. Открой `Настройки`
3. Пройди `Быструю первичную настройку`
4. Открой `Автогенерация`
5. При необходимости выбери:
   - `Настроить автогенерацию`
   - `Импортировать старые посты из файла`
6. Выбери `Запустить автопост сейчас`

CLI-эквивалент для первого автопоста:

```bash
python -m daily_poster autopilot --force
```

### Автозапуск на macOS

Через TUI можно настроить ежедневный запуск прямо из `Настройки -> Расписание публикаций`.

В проекте уже есть `launchd`-шаблоны:

- [automation/com.codex.daily-poster.plist](/Users/artem/Documents/Codex/2026-04-27/codex/automation/com.codex.daily-poster.plist)
- [automation/com.codex.maybe-poster.plist](/Users/artem/Documents/Codex/2026-04-27/codex/automation/com.codex.maybe-poster.plist)
- [automation/com.codex.autopilot.plist](/Users/artem/Documents/Codex/2026-04-27/codex/automation/com.codex.autopilot.plist)

Если хочешь руками:

```bash
mkdir -p ~/Library/LaunchAgents
cp automation/com.codex.autopilot.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.codex.autopilot.plist
```

## Windows

### Установка

Открой PowerShell:

```powershell
git clone <URL_РЕПОЗИТОРИЯ>
cd <ИМЯ_ПАПКИ_РЕПО>
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
Copy-Item .env.example .env
```

Если PowerShell ругается на execution policy, можно на текущую сессию:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
```

Пакет `windows-curses` подтянется автоматически через зависимости проекта, так что `tgauto` будет работать и на Windows.

Заполни `.env`:

```env
OPENAI_API_KEY=sk-...
TELEGRAM_BOT_TOKEN=123456789:ABC...
TELEGRAM_CHAT_ID=@your_channel
ACTIVE_MODE=autogen
```

### Первый автогенерированный пост

```powershell
tgauto
```

Дальше:

1. `Настройки`
2. `Быстрая первичная настройка`
3. `Автогенерация`
4. `Запустить автопост сейчас`

CLI-эквивалент:

```powershell
python -m daily_poster autopilot --force
```

### Автозапуск на Windows

Для фонового запуска используй `Task Scheduler`.

Пример задачи раз в час:

```powershell
schtasks /Create /SC HOURLY /MO 1 /TN "TG Auto Autopilot" /TR "\"%CD%\\.venv\\Scripts\\python.exe\" -m daily_poster autopilot" /F
```

Если хочешь режим отслеживания раз в день, используй вместо `autopilot` команду:

```powershell
-m daily_poster publish
```

## Linux

### Установка

Открой терминал:

```bash
git clone <URL_РЕПОЗИТОРИЯ>
cd <ИМЯ_ПАПКИ_РЕПО>
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
cp .env.example .env
```

Заполни `.env`:

```env
OPENAI_API_KEY=sk-...
TELEGRAM_BOT_TOKEN=123456789:ABC...
TELEGRAM_CHAT_ID=@your_channel
ACTIVE_MODE=autogen
```

### Первый автогенерированный пост

```bash
tgauto
```

Потом:

1. `Настройки`
2. `Быстрая первичная настройка`
3. `Автогенерация`
4. `Запустить автопост сейчас`

CLI-эквивалент:

```bash
python -m daily_poster autopilot --force
```

### Автозапуск на Linux

Самый простой путь - `cron`.

Пример фоновой проверки раз в час:

```bash
crontab -e
```

И строка:

```cron
0 * * * * cd <ABS_PATH_TO_REPO> && <ABS_PATH_TO_REPO>/.venv/bin/python -m daily_poster autopilot >> <ABS_PATH_TO_REPO>/logs/autopilot.log 2>&1
```

Если нужен режим отслеживания раз в день:

```cron
0 21 * * * cd <ABS_PATH_TO_REPO> && <ABS_PATH_TO_REPO>/.venv/bin/python -m daily_poster publish >> <ABS_PATH_TO_REPO>/logs/daily.log 2>&1
```

При желании можно использовать и `systemd --user`, но для первого запуска `cron` обычно быстрее и понятнее.

## Импорт старой истории канала

Если хочешь, чтобы бот понимал стиль канала по старым постам, можно импортировать историю:

```bash
python -m daily_poster import-history path/to/channel-history.json
```

Поддерживаются:

- `.txt`
- `.md`
- `.json`
- `.jsonl`

То же самое доступно в `Автогенерация -> Импортировать старые посты из файла`.

## Полезные команды

```bash
python -m daily_poster env-check
python -m daily_poster doctor
python -m daily_poster preview --save
python -m daily_poster publish
python -m daily_poster autopilot --preview --save
python -m daily_poster autopilot --force
python -m daily_poster sync-channel
python -m daily_poster import-history path/to/channel-history.json
python -m daily_poster topic-post "Тема поста" --preview --save
```

## Как хранится память

Локальная память канала:

- [data/memory.json](/Users/artem/Documents/Codex/2026-04-27/codex/data/memory.json)
- [data/posts.jsonl](/Users/artem/Documents/Codex/2026-04-27/codex/data/posts.jsonl)

Она нужна, чтобы:

- не повторять одни и те же темы;
- удерживать стиль канала;
- помнить недавние смыслы и ходы;
- использовать тон уже опубликованных сообщений.

## Как работает режим отслеживания

Бот читает только те папки, которые указаны в `ACTIVITY_SCAN_ROOTS`.

Он:

- пропускает `.env`, токены, private keys и похожие секреты;
- пропускает мусорные каталоги вроде `.git`, `node_modules`, `venv`;
- не отправляет бинарное содержимое PDF/ZIP/картинок в OpenAI;
- для непрочитываемых файлов использует метаданные: имя, путь, размер, даты.

После каждого успешного поста checkpoint обновляется. Все старые изменения считаются уже учтенными.

Если сигнал слабый, например почти ничего не менялось, бот должен писать честно и спокойно, а не выдумывать деятельность.

## Полезные команды

Хотя основной сценарий идет через TUI, CLI тоже остается:

```bash
python3 -m daily_poster env-check
python3 -m daily_poster doctor
python3 -m daily_poster preview --save
python3 -m daily_poster publish
python3 -m daily_poster topic-post "Почему трейты в Scala не просто интерфейсы" --preview --save
python3 -m daily_poster autopilot --preview --save
python3 -m daily_poster sync-channel
python3 -m daily_poster import-history path/to/channel-history.json
```


Через `tgauto` можно:

- поменять ежедневное время постинга;
- включить автогенерацию;
- запускать посты вручную;
- смотреть диагностику и логи.

## Структура проекта

- [daily_poster/__main__.py](/Users/artem/Documents/Codex/2026-04-27/codex/daily_poster/__main__.py) - ядро продукта и CLI
- [daily_poster/tui.py](/Users/artem/Documents/Codex/2026-04-27/codex/daily_poster/tui.py) - terminal UI
- [config/post_prompt.md](/Users/artem/Documents/Codex/2026-04-27/codex/config/post_prompt.md) - редакционная инструкция для автора
- [.env.example](/Users/artem/Documents/Codex/2026-04-27/codex/.env.example) - пример конфигурации
- [tests/test_core.py](/Users/artem/Documents/Codex/2026-04-27/codex/tests/test_core.py) - базовые unit-тесты



Для связи и уточнений используйте ТГ автора @parano1c
