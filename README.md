# Telegram Daily AI Poster

Небольшой проект, который каждый день смотрит файлы, измененные с прошлого поста, генерирует по ним отчет через OpenAI API и публикует его в Telegram-канал через Telegram Bot API.

Поддерживает:

- предпросмотр поста без публикации;
- публикацию в канал;
- сбор рабочего контекста из файлов, измененных с прошлого поста;
- сохранение истории опубликованных постов;
- настройку стиля через `config/post_prompt.md`;
- ежедневный запуск через `cron` или macOS `launchd`.

## 1. Что нужно заранее

1. OpenAI API key: создай ключ в личном кабинете OpenAI.
2. Telegram-канал, куда будут выходить посты.
3. Telegram-бот:
   - открой `@BotFather` в Telegram;
   - выполни `/newbot`;
   - сохрани токен вида `123456:ABC...`;
   - добавь бота администратором в свой канал;
   - дай ему право публиковать сообщения.

## 2. Настройка проекта

Открой терминал в папке проекта:

```bash
cd /Users/artem/Documents/Codex/2026-04-27/codex
```

Скопируй пример переменных окружения:

```bash
cp .env.example .env
```

Открой `.env` и заполни значения:

```bash
OPENAI_API_KEY=sk-...
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_CHAT_ID=@your_channel_username
ACTIVITY_SCAN_ROOTS=~/Documents,~/Desktop,~/Downloads
```

Если канал приватный, `@username` не подойдет. Тогда временно опубликуй что-нибудь в канал, перешли пост боту `@userinfobot` или используй любой способ узнать numeric chat id канала. Обычно он выглядит примерно так:

```bash
TELEGRAM_CHAT_ID=-1001234567890
```

## 3. Настрой стиль канала

Отредактируй файл:

```bash
config/post_prompt.md
```

Там задается стиль ежедневного отчета: как писать о задачах, проектах, языках программирования, выводах дня и том, чего не стоит раскрывать.

## 4. Настрой папки для сканирования

В `.env` есть настройка:

```bash
ACTIVITY_SCAN_ROOTS=~/Documents,~/Desktop,~/Downloads
```

Это список папок через запятую. Каждый день бот ищет там файлы, измененные с прошлого опубликованного поста, читает текстовые файлы и собирает краткий контекст для поста.

Например, если твои проекты лежат в `~/Projects` и `~/Documents/Codex`, можно поставить:

```bash
ACTIVITY_SCAN_ROOTS=~/Projects,~/Documents/Codex
```

Есть защитные ограничения:

- пропускаются `.env`, ключи, токены, архивы, картинки, видео, бинарные файлы;
- пропускаются папки вроде `node_modules`, `.git`, `venv`, `Library`;
- берется не весь диск целиком, а только заданные папки;
- объем текста ограничен настройками `ACTIVITY_MAX_FILES`, `ACTIVITY_MAX_CHARS_PER_FILE`, `ACTIVITY_MAX_TOTAL_CHARS`.

Посмотреть, что бот увидит с прошлого поста, без запроса к OpenAI:

```bash
python3 -m daily_poster context
```

## 5. Проверка без публикации

Сгенерировать пост и просто вывести его в терминал:

```bash
python3 -m daily_poster preview
```

Сгенерировать пост и сохранить черновик в `data/drafts`:

```bash
python3 -m daily_poster preview --save
```

## 6. Публикация

Сгенерировать и сразу опубликовать пост:

```bash
python3 -m daily_poster publish
```

Опубликовать конкретный текст из файла:

```bash
python3 -m daily_poster send-file data/drafts/2026-04-27.md
```

## 7. Ежедневный запуск через cron

Открой cron:

```bash
crontab -e
```

Добавь строку для публикации каждый день в 21:00:

```cron
0 21 * * * cd /Users/artem/Documents/Codex/2026-04-27/codex && /usr/bin/python3 -m daily_poster publish >> logs/daily.log 2>&1
```

Создай папку логов, если ее еще нет:

```bash
mkdir -p logs
```

## 8. Ежедневный запуск через macOS launchd

В проекте есть шаблон:

```bash
automation/com.codex.daily-poster.plist
```

Скопируй его в `~/Library/LaunchAgents`:

```bash
cp automation/com.codex.daily-poster.plist ~/Library/LaunchAgents/
```

Загрузи расписание:

```bash
launchctl load ~/Library/LaunchAgents/com.codex.daily-poster.plist
```

Остановить:

```bash
launchctl unload ~/Library/LaunchAgents/com.codex.daily-poster.plist
```

По умолчанию launchd-запуск стоит на 21:00 каждый день.

## 9. Команды

```bash
tgauto
python3 -m daily_poster context
python3 -m daily_poster preview
python3 -m daily_poster preview --save
python3 -m daily_poster publish
python3 -m daily_poster maybe-post
python3 -m daily_poster autopilot --preview --save
python3 -m daily_poster sync-channel
python3 -m daily_poster topic-post "Трейты в Scala" --preview --save
python3 -m daily_poster memory
python3 -m daily_poster send-file path/to/post.md
python3 -m daily_poster env-check
```

`tgauto` открывает терминальный интерфейс, где можно:

- настроить `.env`;
- изменить prompt бота;
- выбрать модель и увидеть примерную стоимость;
- поменять расписание ежедневного поста;
- посмотреть рабочий контекст с прошлого поста;
- открыть список измененных с прошлого поста файлов и посмотреть diff/details по выбранному файлу;
- сгенерировать preview;
- сгенерировать free preview — свободную мысль без файлового контекста;
- написать тему/мысль и получить пост по ней;
- посмотреть локальную память канала;
- запустить maybe-post: свободный или рабочий пост с учетом минимальной паузы;
- настроить автоведение канала: лимит постов в день, паузу, sync памяти и launchd-агент;
- опубликовать отчет прямо сейчас;
- настроить канал Telegram.

Если команда `tgauto` не находится в уже открытом терминале, открой новый терминал или выполни:

```bash
source ~/.zshrc
```

Для файлов внутри git-репозитория экран изменений показывает `git diff`. Для обычных текстовых файлов без git бот хранит локальные снимки в `data/snapshots`: первый просмотр сохраняет baseline, а после следующего изменения будет показан local diff. Файлы вроде PDF, ZIP и картинок показываются как `download`/`binary`: бот передает в контекст имя, путь, расширение, размер, дату создания и дату изменения, но не читает их бинарное содержимое.

После успешной публикации через `publish`, `send-file` или кнопку Publish now в TUI бот обновляет checkpoint. Все текущие файлы считаются уже учтенными, и следующий отчет будет смотреть только новые изменения после этого момента. Если изменений нет или сигнал слабый, например была только скачана картинка, бот должен честно написать спокойный отчет без выдуманных задач.

## 10. Где что лежит

- `config/post_prompt.md` - редакционная политика и стиль постов.
- `.env` - секреты и настройки, не коммитить.
- `.env.example` - пример настроек.
- `data/drafts` - сохраненные черновики.
- `data/posts.jsonl` - история публикаций.
- `data/state.json` - checkpoint последнего опубликованного поста.
- `data/memory.json` - локальная память канала: частые темы, последние посты, стилевые заметки.
- `logs` - логи при запуске по расписанию.

## 11. Полезные замечания

- Telegram ограничивает одно сообщение примерно 4096 символами. Скрипт проверяет длину и попросит укоротить пост, если модель разошлась.
- Если хочешь сначала утверждать посты вручную, используй `preview --save`, редактируй файл, потом публикуй через `send-file`.
- Если OpenAI API вернул ошибку по модели, поменяй `OPENAI_MODEL` в `.env`.
- Чем точнее заданы `ACTIVITY_SCAN_ROOTS`, тем лучше отчет. Если сканировать слишком широкие папки, в контекст попадет много шума.
- `python3 -m daily_poster digest` показывает локальный score активности с прошлого поста.
- `python3 -m daily_poster doctor` проверяет основные настройки и предупреждает о шумных/дорогих сценариях.
- `python3 -m daily_poster mark-checkpoint` помечает текущие файлы как уже учтенные без публикации.
- `python3 -m daily_poster maybe-post` может опубликовать рабочий или свободный авторский пост, но уважает `SPONTANEOUS_MIN_PAUSE_HOURS`.
- `python3 -m daily_poster autopilot --preview --save` генерирует черновик автопоста в стиле канала.
- `python3 -m daily_poster autopilot` запускает автоведение один раз с учетом лимитов.
- `python3 -m daily_poster sync-channel` импортирует новые Telegram `channel_post` updates в локальную память.
- `python3 -m daily_poster topic-post "моя мысль" --preview --save` делает пост по твоей теме без публикации.
- `python3 -m daily_poster topic-post "моя мысль"` публикует пост по теме сразу.
- `python3 -m daily_poster memory` показывает локальную память канала.
- Перед отправкой в OpenAI текстовые выдержки проходят простую редацию секретов: API keys, токены, passwords и private keys заменяются на `[REDACTED]`.
- Автотесты запускаются так: `python3 -m unittest discover -s tests -v`.

Спонтанные посты настраиваются в `.env`:

```bash
SPONTANEOUS_ENABLED=true
SPONTANEOUS_MIN_PAUSE_HOURS=6
```

Фоновый агент `automation/com.codex.maybe-poster.plist` раз в час запускает `maybe-post`. Сам `maybe-post` не обязан публиковать каждый раз: он проверяет паузу, активность и иногда решает промолчать.

Автоведение канала настраивается в `.env`:

```bash
AUTOPILOT_ENABLED=false
AUTOPILOT_POSTS_PER_DAY=2
AUTOPILOT_MIN_PAUSE_HOURS=4
```

Фоновый агент `automation/com.codex.autopilot.plist` раз в час запускает `autopilot`. Он не публикует сверх лимита и не нарушает минимальную паузу. Telegram Bot API не отдает старую историю канала задним числом, поэтому `sync-channel` импортирует только новые `channel_post` updates, которые бот получает после настройки. Старую историю можно добавить через локальную `data/posts.jsonl`/`data/memory.json` или экспортом.

Посты по теме можно задавать на русском:

```bash
python3 -m daily_poster topic-post "Почему трейты в Scala не просто интерфейсы" --preview --save
```

В TUI пункт `Пост по теме` тоже принимает русский ввод.
