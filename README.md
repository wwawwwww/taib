# TG Auto

`TG Auto` - это локальный AI-бот для ведения Telegram-канала.

Он умеет:

- писать посты в стилистике канала;
- помнить прошлые посты и локальную память канала;
- делать свободные автопосты;
- собирать контекст по изменениям в папках;
- публиковать посты по заданной теме;
- импортировать старую историю канала из файла.

Главная идея продукта: пользователь запускает один скрипт под свою систему, попадает в TUI `tgauto`, настраивает ключи и канал, а дальше работает уже через понятное меню.

## Что нужно заранее

Для любой системы потребуется:

1. `git`
2. Python `3.10+`
3. OpenAI API key
4. Telegram-бот от `@BotFather`
5. Telegram-канал, куда бот будет публиковать сообщения
6. Бот должен быть администратором канала

## Быстрый запуск через скрипты

В репозитории есть три bootstrap-скрипта:

- [scripts/bootstrap-macos.sh](/Users/artem/Documents/Codex/2026-04-27/codex/scripts/bootstrap-macos.sh)
- [scripts/bootstrap-linux.sh](/Users/artem/Documents/Codex/2026-04-27/codex/scripts/bootstrap-linux.sh)
- [scripts/bootstrap-windows.ps1](/Users/artem/Documents/Codex/2026-04-27/codex/scripts/bootstrap-windows.ps1)

Они делают одно и то же:

- создают `.venv`, если его еще нет;
- ставят зависимости;
- устанавливают проект в editable-режиме;
- создают `.env` из `.env.example`, если файла еще нет;
- создают папку `logs`;
- запускают TUI `tgauto`.

## Меню TUI

После запуска `tgauto` ты увидишь пять основных действий.

### 1. `Автогенерация`

Режим для канала, который бот ведет как автор:

- пишет свободные посты в стиле канала;
- использует локальную память и базу канала;
- может публиковать спонтанные посты с минимальной паузой;
- умеет заменить базу канала из Telegram-экспорта;
- не зависит от сканирования рабочих папок.

Внутри раздела можно:

- включить режим `autogen`;
- настроить лимиты и паузы;
- загрузить `Базу канала`;
- форсировать автопост прямо сейчас;

`База канала` - это не счетчик публикаций и не сегодняшняя активность. Это заменяемый корпус старых сообщений, по которому бот строит профиль голоса: пунктуацию, длину, регистр, сленг, частые тематические поля и характерные слова. В генерацию не подставляется простыня старых постов как примеры для пересборки. База нужна для манеры, а не для копирования старых сюжетов. При новой загрузке старая база очищается.

### 2. `Отслеживание`

Режим для канала-дневника работы:

- следит за выбранными папками;
- читает текстовые файлы и метаданные бинарных файлов;
- после публикации обновляет checkpoint;
- новый пост строится только по изменениям после прошлого поста.

Внутри раздела можно:

- включить режим `tracking`;
- выбрать папки для отслеживания;
- посмотреть сводку активности;
- опубликовать пост по контексту сразу.

### 3. `Пост по теме`

Ручной режим:

- ты задаешь тему, мысль или тезис;
- бот пишет и публикует пост на эту тему после подтверждения.

### 4. `Настройки`

Центр конфигурации продукта:

- быстрая первичная настройка;
- OpenAI API key;
- Telegram bot token и канал;
- модель и примерная стоимость;
- prompt автора;
- папки и лимиты отслеживания;
- диагностика.

### 5. `Остановить бота`

Аварийная и понятная кнопка остановки:

- выключает `ACTIVE_MODE`;
- отключает `AUTOPILOT_ENABLED` и `SPONTANEOUS_ENABLED`;
- снимает фоновые launchd-агенты на macOS;
- завершает текущие процессы публикации `daily_poster`.

## Важное правило режимов

`Автогенерация` и `Отслеживание` взаимоисключающие.

Активен только один режим:

- `ACTIVE_MODE=autogen`
- `ACTIVE_MODE=tracking`

## macOS: от клона репозитория до первого автопоста

### 1. Клонирование

```bash
git clone <URL_РЕПОЗИТОРИЯ>
cd <ИМЯ_ПАПКИ_РЕПО>
chmod +x scripts/bootstrap-macos.sh
```

### 2. Запуск bootstrap-скрипта

```bash
./scripts/bootstrap-macos.sh
```

Что произойдет:

- создастся `.venv`;
- установятся зависимости;
- появится `.env`, если его еще нет;
- откроется `tgauto`.

### 3. Первичная настройка в TUI

В TUI:

1. Открой `Настройки`
2. Выбери `Быстрая первичная настройка`
3. Введи:
   - `OPENAI_API_KEY`
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
4. Выбери модель
5. Выбери режим `Автогенерация`

### 4. Первый автопост

Открой:

- `Автогенерация`
- `Запустить автопост сейчас`

CLI-эквивалент:

```bash
. .venv/bin/activate
python -m daily_poster autopilot --force
```

### 5. Автозапуск на macOS

Через TUI:

- `Настройки`
- `Расписание публикаций`

Для macOS используются шаблоны `launchd`:

- [automation/com.codex.daily-poster.plist](/Users/artem/Documents/Codex/2026-04-27/codex/automation/com.codex.daily-poster.plist)
- [automation/com.codex.maybe-poster.plist](/Users/artem/Documents/Codex/2026-04-27/codex/automation/com.codex.maybe-poster.plist)
- [automation/com.codex.autopilot.plist](/Users/artem/Documents/Codex/2026-04-27/codex/automation/com.codex.autopilot.plist)

## Windows: от клона репозитория до первого автопоста

### 1. Клонирование

Открой PowerShell:

```powershell
git clone <URL_РЕПОЗИТОРИЯ>
cd <ИМЯ_ПАПКИ_РЕПО>
```

Если PowerShell ругается на execution policy:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
```

### 2. Запуск bootstrap-скрипта

```powershell
.\scripts\bootstrap-windows.ps1
```

Скрипт:

- создаст `.venv`;
- установит проект;
- подтянет `windows-curses`;
- создаст `.env`, если его еще нет;
- запустит `tgauto`.

### 3. Первичная настройка в TUI

В TUI:

1. `Настройки`
2. `Быстрая первичная настройка`
3. Введи:
   - `OPENAI_API_KEY`
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
4. Переключись в `Автогенерация`

### 4. Первый автопост

Открой:

- `Автогенерация`
- `Запустить автопост сейчас`

CLI-эквивалент:

```powershell
.venv\Scripts\python.exe -m daily_poster autopilot --force
```

### 5. Автозапуск на Windows

Для Windows используй `Task Scheduler`.

Пример hourly-задачи:

```powershell
schtasks /Create /SC HOURLY /MO 1 /TN "TG Auto Autopilot" /TR "\"%CD%\\.venv\\Scripts\\python.exe\" -m daily_poster autopilot" /F
```

Для режима отслеживания раз в день вместо `autopilot` используй:

```powershell
-m daily_poster publish
```

## Linux: от клона репозитория до первого автопоста

### 1. Клонирование

```bash
git clone <URL_РЕПОЗИТОРИЯ>
cd <ИМЯ_ПАПКИ_РЕПО>
chmod +x scripts/bootstrap-linux.sh
```

### 2. Запуск bootstrap-скрипта

```bash
./scripts/bootstrap-linux.sh
```

Скрипт:

- создаст `.venv`;
- установит зависимости;
- создаст `.env`, если его еще нет;
- запустит `tgauto`.

### 3. Первичная настройка в TUI

В TUI:

1. `Настройки`
2. `Быстрая первичная настройка`
3. Введи:
   - `OPENAI_API_KEY`
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
4. Переключись в `Автогенерация`

### 4. Первый автопост

Открой:

- `Автогенерация`
- `Запустить автопост сейчас`

CLI-эквивалент:

```bash
. .venv/bin/activate
python -m daily_poster autopilot --force
```

### 5. Автозапуск на Linux

Самый простой путь - `cron`.

Открой:

```bash
crontab -e
```

Добавь hourly-проверку:

```cron
0 * * * * cd <ABS_PATH_TO_REPO> && <ABS_PATH_TO_REPO>/.venv/bin/python -m daily_poster autopilot >> <ABS_PATH_TO_REPO>/logs/autopilot.log 2>&1
```

Для режима отслеживания раз в день:

```cron
0 21 * * * cd <ABS_PATH_TO_REPO> && <ABS_PATH_TO_REPO>/.venv/bin/python -m daily_poster publish >> <ABS_PATH_TO_REPO>/logs/daily.log 2>&1
```

## База канала

Если хочешь, чтобы бот писал в манере существующего канала, загрузи базу канала:

```bash
python -m daily_poster import-history path/to/channel-history.json
```

Каждая новая загрузка заменяет предыдущую базу. Старые импортированные сообщения удаляются из локальной памяти стиля и не считаются постами, которые бот отправил сегодня.

После загрузки TG Auto анализирует архив и сохраняет не сами посты как сценарии для повторения, а профиль стиля:

- среднюю длину и структуру постов;
- пунктуацию, регистр, эмодзи, хэштеги и упоминания;
- характерные слова, сленг и тематические поля;
- фрагменты, которые нельзя повторять.

Уже опубликованные ботом посты остаются в памяти отдельно, чтобы следующие посты не повторяли старые темы и формулировки.

Поддерживаются форматы:

- `.txt`
- `.md`
- `.json`
- `.jsonl`
- `.html`
- `.htm`

То же самое доступно в:

- `Автогенерация`
- `База канала`

Можно указывать не только один файл, но и целую папку с экспортом Telegram. Если у тебя архив состоит из `messages.html`, `messages2.html`, `messages3.html`, просто укажи путь к этой папке.

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

## Локальная память и файлы

Ключевые файлы проекта:

- [daily_poster/__main__.py](/Users/artem/Documents/Codex/2026-04-27/codex/daily_poster/__main__.py)
- [daily_poster/tui.py](/Users/artem/Documents/Codex/2026-04-27/codex/daily_poster/tui.py)
- [config/post_prompt.md](/Users/artem/Documents/Codex/2026-04-27/codex/config/post_prompt.md)
- [.env.example](/Users/artem/Documents/Codex/2026-04-27/codex/.env.example)
- [tests/test_core.py](/Users/artem/Documents/Codex/2026-04-27/codex/tests/test_core.py)

Локальная память канала хранится здесь:

- `data/memory.json`
- `data/posts.jsonl`

## Проверка проекта

```bash
python -m unittest discover -s tests -v
python -m compileall daily_poster tests
python -m daily_poster doctor
```

## Важные замечания

- OpenAI API оплачивается отдельно от подписки ChatGPT.
- `sync-channel` подтягивает только доступные Telegram updates, а не старую историю канала задним числом.
- Для большинства сценариев хороший дефолт: `OPENAI_MODEL=gpt-5-mini`.
- Если хочешь уменьшить стоимость, сначала сужай `ACTIVITY_SCAN_ROOTS`, а не только меняй модель.
