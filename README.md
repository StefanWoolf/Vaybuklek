# 🎼 Дирижёр

**Автономный AI-проджект-менеджер для Telegram-команд и онлайн-встреч.**

Дирижёр читает командный чат, слушает встречи, сам извлекает задачи, дедлайны
и ответственных, ведёт канбан-доску в YouGile, напоминает о сроках и сверяет
вечерние отчёты с реальным статусом задач. Человек только подтверждает и правит.

> Метафора: дирижёр не играет на инструментах, но без него оркестр играет
> вразнобой. Бот не делает работу за команду — он её синхронизирует.

Это реализация по отчёту «Этап 1 · Problem & Solution Validation».

---

## Ключевая идея реализации

- **Mock-first.** Любой внешний сервис (Telegram, LLM, YouGile, аудио) при
  отсутствии ключа автоматически переходит в mock-режим. Система **полностью
  запускается и демонстрируется офлайн** — ключи подключаются позже через `.env`.
  Ни одного секрета в коде нет.
- **Чистое Python-ядро** (тестируемое, один деплой), а **n8n** — слой
  оркестрации внешних триггеров (Telegram webhook + cron), который дёргает
  HTTP-эндпоинты ядра. См. [`n8n/dirizher-orchestration.json`](n8n/dirizher-orchestration.json).
- **Строгий JSON + порог уверенности.** LLM возвращает жёсткую схему с полем
  `confidence`; при `confidence < 0.7` бот не выдумывает задачу, а уточняет.
- **Подтверждение и флаг авто-режима (True/False)** — как в отчёте (раздел 3.4).

---

## Архитектура

Трёхслойный конвейер: **источники → память и оркестрация → действия**.

```
 Telegram чат ─┐                                  ┌─► YouGile (карточки)
 Голосовые    ─┼─► Извлечение задач (LLM, строгий │
 Встречи      ─┘    JSON + confidence)            ├─► Telegram (подтверждения,
        │                  │                       │   напоминания, сводки)
        │                  ▼                       │
   noisereduce →    Дедуп (ChromaDB/лексич.)       │
   pyannote →       Снимок проекта (MD)            │
   Whisper          Контроль нагрузки  ────────────┘
                         ▲
        n8n (webhook-роутинг + cron) ──► HTTP API ядра
```

### Структура кода

```
src/dirizher/
  config.py            конфигурация из .env (pydantic-settings), mock-детект
  container.py         сборка зависимостей + per-chat режим/состояние
  main.py              точка входа (симулятор | polling | webhook)
  domain/              модели (Task, ExtractedTask, …) и enum'ы
  llm/                 провайдеры (mock/groq/gigachat), промпт, строгий парсинг
  memory/              дедуп (ChromaDB + лексический fallback), MD-снимок проекта
  integrations/        YouGile API (mock-доска + боевой REST)
  services/            task_service (ядро), reconciliation, meeting
  bot/                 aiogram: хендлеры, сценарий подтверждения/правки, клавиатуры
  scheduler/           APScheduler: напоминания, вечерняя сверка
  audio/               noisereduce → pyannote → Whisper, реестр голосов
  api/                 FastAPI-эндпоинты для n8n
  cli/                 консольный симулятор и обработчик записи встречи
n8n/                   импортируемый workflow оркестрации
tests/                 юнит-тесты ядра
```

---

## Быстрый старт (демо без ключей)

```bash
python -m venv .venv && .venv\Scripts\activate      # Windows
pip install -e .                                     # ядро
```

### 1. Консольный симулятор — вся цепочка «чат → задача → доска»

```bash
python -m dirizher.cli.simulator
```

```
you> Максим, сделай авторизацию к четвергу
🆕 Новая задача · 📋 Сделай авторизацию · 👤 maxim · 📅 2026-06-11 · 🎯 0.95
  Создать карточку? [Y/n] y   ✅ Создано на доске: mock-0001
you> /board
you> /mode auto        # переключить в авто-режим (без подтверждений)
```

### 2. Обработка записи встречи → саммари → задачи

```bash
python -m dirizher.cli.meeting путь/к/транскрипту.txt
```

Формат `.txt`: строки `Имя: реплика`. С реальным аудио (`.wav/.ogg`) и
`DIRIZHER_AUDIO__ENABLED=true` запускается пайплайн noisereduce → pyannote → Whisper.

### 3. Тесты

```bash
pip install -e ".[dev]"
pytest -q
```

---

## Боевой запуск

```bash
cp .env.example .env      # заполнить ключи (см. ниже)
pip install -e ".[llm,memory]"   # + audio при необходимости
python -m dirizher.main
```

Минимум для боевого режима — задать `DIRIZHER_TELEGRAM__BOT_TOKEN`. Всё
остальное опционально и деградирует в mock. Ключевые переменные:

| Переменная | Назначение |
|---|---|
| `DIRIZHER_TELEGRAM__BOT_TOKEN` | токен бота от @BotFather |
| `DIRIZHER_TELEGRAM__MODE` | `polling` (dev) или `webhook` (n8n) |
| `DIRIZHER_LLM__PROVIDER` | `mock` \| `groq` \| `gigachat` |
| `DIRIZHER_LLM__GROQ_API_KEY` | ключ Groq (Llama 3.3 70B) |
| `DIRIZHER_LLM__GIGACHAT_CREDENTIALS` | креды GigaChat |
| `DIRIZHER_YOUGILE__API_KEY` | ключ YouGile (+ ID колонок) |
| `DIRIZHER_MEMORY__BACKEND` | `lexical` (без сети) \| `chroma` (семантика) |
| `DIRIZHER_AUDIO__ENABLED` | включить распознавание встреч |

---

## Команды бота

| Команда | Действие |
|---|---|
| _обычное сообщение_ | бот сам находит задачи и предлагает их завести |
| `/mode auto\|manual` | режим отправки: сразу или с подтверждением (флаг True/False) |
| `/board` | показать канбан-доску |
| `/tasks` | мои открытые задачи |
| `/report <текст>` | вечерний отчёт — бот сам проставит статусы |
| `/reconcile` | вечерняя сверка сейчас (тегает не отписавшихся) |
| `/remind` | проверить дедлайны и напомнить |
| `/register Имя; алиасы` | представиться для точной атрибуции |

Сценарий подтверждения: на каждую новую задачу — кнопки **✅ Подтвердить /
✏️ Поправить / ❌ Отклонить**. «Поправить» принимает уточнение текстом или
голосом, бот переформулирует и снова показывает карточку.

---

## n8n как оркестратор

1. Запустите ядро: `DIRIZHER_TELEGRAM__MODE=webhook python -m dirizher.main`
   (API поднимется на `:8080`).
2. Импортируйте [`n8n/dirizher-orchestration.json`](n8n/dirizher-orchestration.json).
3. Задайте в n8n переменные окружения `DIRIZHER_CORE_URL` (например
   `http://localhost:8080`) и `DIRIZHER_SHARED_SECRET`.
4. Workflow:
   - **Telegram Webhook** → `POST /ingest/telegram` (роутинг апдейтов в ядро);
   - **Cron напоминания** → `POST /jobs/reminders`;
   - **Cron вечерняя сверка** → `POST /jobs/evening-reconcile`.

HTTP-эндпоинты ядра (`src/dirizher/api/server.py`): `/health`,
`/ingest/telegram`, `/jobs/reminders`, `/jobs/evening-reconcile`
(защита заголовком `X-Dirizher-Token`).

---

## Соответствие ядру MVP (отчёт, раздел 4)

| Функция MVP (Must have) | Где |
|---|---|
| Бот читает переписку | `bot/handlers/messages.py` |
| LLM извлекает задачи в строгий JSON | `llm/`, схема `domain/models.py:ExtractedTask` |
| Карточки в YouGile + режим подтверждения (True/False) | `integrations/yougile.py`, `bot/flow.py`, `container.py:ModeStore` |
| Проактивные напоминания с тегом @username | `scheduler/jobs.py:run_reminders` |
| Вечерняя сверка отчётов, тег не отписавшихся | `services/reconciliation.py` |
| Дедупликация через ChromaDB | `memory/vector_store.py` |

**Усиления (Should/Could):** аудио встреч (`audio/`), голосовые/кружки
(`bot/handlers/voice.py`), контроль перегрузки (`task_service.workload_warning`),
реестр голосов (`audio/speakers.py`).

---

## Замечания по mock-эвристике

Mock-провайдер (`llm/mock_provider.py`) — детерминированный извлекатель на
правилах: распознаёт исполнителя, дедлайны (дни недели/относительные даты),
приоритет и уверенность. Он покрывает типовые формулировки и обеспечивает
работу демо без сети, но на «живых» развёрнутых фразах со встреч уступает
боевому LLM — для этого и предусмотрен переключатель провайдера. Семантический
дедуп («сделать API» ≡ «разработать эндпоинт») доступен на backend `chroma`;
лексический fallback ловит явные повторы.
