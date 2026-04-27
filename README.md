# tgauto

`tgauto` - локальный AI-инструмент для ведения Telegram-канала от первого лица.

Проект рассчитан на реальное использование: бот пишет посты на русском, помнит историю канала, умеет работать в двух отдельных режимах и управляется через короткий TUI без перегруженного меню.

## Что умеет продукт

У `tgauto` есть четыре пользовательских раздела:

1. `Автогенерация`
2. `Отслеживание`
3. `Пост по теме`
4. `Настройки`

### 1. Автогенерация

Режим для канала, который бот ведет как автор:

- пишет в стилистике канала;
- опирается на локальную память и прошлые посты;
- может публиковать свободные посты несколько раз в день;
- не использует локальные папки как основной источник контента.

### 2. Отслеживание

Режим для канала-дневника работы:

- смотрит выбранные папки на компьютере;
- собирает контекст по изменениям в файлах;
- видит код, текст и метаданные скачанных файлов;
- после публикации фиксирует checkpoint и следующий пост строит только по новым изменениям.

### 3. Пост по теме

Ручной сценарий:

- ты задаешь мысль, тему или тезис;
- бот пишет пост в нужном тоне;
- можно сделать preview или сразу опубликовать.

### 4. Настройки

В одном месте собраны:

- OpenAI API key;
- Telegram bot token и канал;
- модель и примерная стоимость;
- prompt автора;
- расписание;
- папки и лимиты отслеживания;
- диагностика и логи.

## Главное правило режимов

`Автогенерация` и `Отслеживание` взаимоисключающие.

В каждый момент времени активен только один режим:

- `ACTIVE_MODE=autogen`
- `ACTIVE_MODE=tracking`

Это сделано специально, чтобы канал велся предсказуемо и без конфликтующих сценариев.

## Быстрый старт

Открой проект:

```bash
cd /Users/artem/Documents/Codex/2026-04-27/codex
```

Создай `.env`:

```bash
cp .env.example .env
```

Запусти интерфейс:

```bash
tgauto
```

Если команда еще не подхватилась в текущем терминале:

```bash
source ~/.zshrc
```

## Что нужно для запуска

1. OpenAI API key
2. Telegram-бот от `@BotFather`
3. Telegram-канал, куда бот будет публиковать сообщения
4. Бот должен быть администратором канала

Минимальный `.env`:

```env
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-5-mini
TELEGRAM_BOT_TOKEN=123456789:ABC...
TELEGRAM_CHAT_ID=@your_channel
ACTIVE_MODE=tracking
```

## Рекомендуемый первый сценарий

После запуска `tgauto`:

1. Открой `Настройки`
2. Пройди `Быструю первичную настройку`
3. Выбери модель
4. Укажи канал
5. Реши, какой режим нужен сейчас:
   - `Автогенерация`
   - `Отслеживание`

Дальше продуктом можно пользоваться уже через главное меню без CLI-команд.

## Как работает память

Бот хранит локальную память канала в:

- [data/memory.json](/Users/artem/Documents/Codex/2026-04-27/codex/data/memory.json)
- [data/posts.jsonl](/Users/artem/Documents/Codex/2026-04-27/codex/data/posts.jsonl)

Память нужна для того, чтобы:

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
```

## Расписание

Для ежедневного постинга используется `launchd`.

Шаблоны лежат здесь:

- [automation/com.codex.daily-poster.plist](/Users/artem/Documents/Codex/2026-04-27/codex/automation/com.codex.daily-poster.plist)
- [automation/com.codex.maybe-poster.plist](/Users/artem/Documents/Codex/2026-04-27/codex/automation/com.codex.maybe-poster.plist)
- [automation/com.codex.autopilot.plist](/Users/artem/Documents/Codex/2026-04-27/codex/automation/com.codex.autopilot.plist)

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

## Проверка проекта

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall daily_poster tests
```

## Важные замечания

- OpenAI API и ChatGPT подписка - это разные вещи; для API нужен отдельный биллинг.
- Telegram Bot API не отдает задним числом всю историю канала. `sync-channel` подтягивает только доступные updates.
- Если хочешь уменьшить стоимость, сначала сужай `ACTIVITY_SCAN_ROOTS` и только потом меняй язык или стиль prompt.
- Для большинства сценариев хороший дефолт: `OPENAI_MODEL=gpt-5-mini`.
