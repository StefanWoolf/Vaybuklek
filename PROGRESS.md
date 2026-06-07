# 🎼 Дирижёр — рабочий статус (для себя)

> Внутренняя памятка: что сделано, что осталось, как всё устроено и как проверять.
> Источник правды по требованиям — отчёт «Этап 1». Архитектурные решения — ниже.
> Последнее обновление: 2026-06-07 (боевой запуск состоялся).

## Багфиксы по фидбэку — раунд 2 (на main, проверено live Groq)
- #1 Правка задачи больше НЕ затирает заголовок: `apply_correction` меняет только
  срок/время/приоритет/исполнителя; переименование — лишь по явному маркеру
  («назови…», «переименуй…», «название: …»). `services/task_service.py`.
- #3 Мульти-исполнители: «Данила и Андрей …» → отдельная карточка каждому
  (`_split_assignees` + цикл в `ingest`; `_MULTI_NAME_RE` в mock).
- #4/#5 Команды над существующими задачами из чата: «закрой/в работу/удали задачу
  <id|ключевые слова>» → `bot/task_commands.py` (вызывается до извлечения),
  `board.delete_card`, `service.delete_task`, поиск `repo.get_by_card`.
- #7/#9 Анти-спам: `ignore_threshold=0.6` (ниже — молча), сборка из контекста
  только по явным командам-фиксаторам, усилён системный промпт. Проверено:
  «давай сделаем», «может обнову?», «что думаешь?» → 0 задач.
- #8 Убрана строка «🎯 Уверенность» из карточек.
- ОСТАЛОСЬ из списка: #2 (привязка ТГ-аккаунтов к исполнителям доски YouGile —
  нужен дизайн: маппинг chat-user → YouGile user), #6 (уточнение при двух
  одинаковых именах «два Андрея»; если есть @username — тегаем без уточнения).

## Боевой статус (live)
- Папка проекта: `C:\Users\Stefa\Desktop\Vaybuklek` (англ. имя; старую `Вайбуклек` удалить).
- Ключи подключены и проверены доктором: telegram=live `@degree_case_bot`, llm=groq, yougile=live.
- Запуск: `py -m dirizher.main` — работает (polling + API :8080). Нужен `pip install groq`.
- **Исправленные боевые баги:**
  1. YouGile 400 при создании карточки — `deadline` должен быть timestamp в **мс**
     (`{"deadline": <ms>, "withTime": false}`), не строка. Фикс в `integrations/yougile.py:_deadline_obj`.
  2. Правка приоритета «меньше/ниже» не понижала — добавлены относительные ключи в
     `task_service.apply_correction`; LLM переписывает заголовок только если правка похожа на задачу.
  3. Тесты ходили в боевой Groq → `tests/conftest.py` форсит mock (env-override). 15/15 offline.
- Удаление карточек YouGile: НЕ DELETE, а `PUT /tasks/{id} {"deleted": true}`.
- После правок кода бот нужно перезапустить (Ctrl+C → `py -m dirizher.main`).

## Новые фичи (после первого боевого прогона)
- **Знакомство с командой** (`bot/handlers/onboarding.py`): бот приветствует себя при
  добавлении в группу и при входе новых участников; кнопка «👋 Представиться»
  (`IntroCD`) сохраняет user_id↔@username↔имя. `/join` и `/start` тоже показывают кнопку.
  Если задача на неизвестного — в карточке кнопка «👋 Это я (Имя)»: нажавший
  закрепляется (alias=Имя) и открытые задачи перевешиваются на него.
- **Контекст переписки** (`chat_history.py` + промпт): скользящее окно последних
  ~12 реплик передаётся в LLM. Команды «поставь таску / сделай задачу» собирают
  задачу из обсуждения выше (проверено live: диалог про созвон → «Сделай задачу»
  → задача на четверг). Если из контекста ничего — бот просит уточнить, не молчит.
  Telegram не даёт список участников ботам → знакомство только интерактивное.

---

## TL;DR

- **Реализовано ядро MVP целиком + усиления.** Система запускается и
  демонстрируется **офлайн** (mock-режим), тесты зелёные (15/15).
- **Ключей пока нет** (по договорённости — подключаем на следующем шаге через `.env`).
- **Стек:** чистое Python-ядро (aiogram 3 + APScheduler + FastAPI + pydantic),
  **n8n** — слой webhook/cron-триггеров поверх HTTP-API ядра.
- **Принцип mock-first:** нет ключа → компонент в mock, всё работает.

---

## ✅ Что сделано (по этапам)

### Этап 0 — Каркас
- [x] Структура пакета `src/dirizher`, `pyproject.toml`, `.gitignore`, `.env.example`
- [x] Конфиг `config.py` (pydantic-settings, секции `__`, `effective_provider`, `is_mock`, `mode_banner`)
- [x] Домен: `domain/models.py` (`ExtractedTask`, `Task`, `TeamMember`, `SourceRef`), `domain/enums.py`
- [x] Логирование `logging_setup.py`

### Этап 1 — Ядро «чат → LLM → подтверждение → YouGile»
- [x] LLM: `llm/` — `mock_provider` (эвристика), `groq_provider`, `gigachat_provider`,
      общий `prompt.py`, надёжный `parsing.py`, фабрика `extractor.build_provider`
- [x] Строгий JSON + поле `confidence`; порог 0.7 → outcome `low_confidence`
- [x] Память: `memory/vector_store.py` (ChromaDB + лексический fallback),
      `memory/project_snapshot.py` (живой MD-снимок)
- [x] YouGile: `integrations/yougile.py` — `MockBoard` + `YouGileBoard` за протоколом `BoardClient`
- [x] Репозиторий и реестр команды: `repository.py` (`TaskRepository`, `TeamRegistry.resolve`)
- [x] Ядро: `services/task_service.py` — ingest → классификация (new/duplicate/low_confidence)
      → create/merge/edit/apply_correction; `workload_warning`
- [x] Бот: `bot/` — хендлеры (`commands`, `messages`, `callbacks`, `voice`),
      сценарий подтверждения **✅/✏️/❌**, флаг авто-режима **True/False** (`ModeStore`),
      FSM-правка текстом и голосом, `flow.present` (общий предъявитель)

### Этап 2 — Проактивность
- [x] `scheduler/` — APScheduler, `run_reminders` (тег @username), `run_evening_reconciliation`
- [x] `services/reconciliation.py` — приём отчётов, простановка статусов, дайджест + тег не отписавшихся
- [x] Команды `/report`, `/reconcile`, `/remind`; контроль перегрузки в `flow`/`callbacks`

### Этап 3 — Аудио-усиления
- [x] `audio/transcriber.py` (протокол + Mock), `audio/pipeline.py` (noisereduce → pyannote → Whisper, ленивые импорты)
- [x] `audio/speakers.py` — реестр голосовых отпечатков (enroll/identify)
- [x] `services/meeting.py` — транскрипт → саммари → задачи; `cli/meeting.py`
- [x] Голосовые/кружки: `bot/handlers/voice.py` (в mock честно деградирует)

### Этап 4 — n8n, API, тесты, README
- [x] `api/server.py` — FastAPI: `/health`, `/ingest/telegram`, `/jobs/reminders`, `/jobs/evening-reconcile` (+ `X-Dirizher-Token`)
- [x] `main.py` — симулятор | polling+API | webhook+API, всё + планировщик
- [x] `n8n/dirizher-orchestration.json` — webhook-роутинг + 2 cron-триггера
- [x] `tests/` — 15 тестов (extraction, service, reconciliation), все зелёные
- [x] `README.md` — архитектура, запуск, команды, маппинг на MVP

---

## ⏳ Что осталось / следующие шаги

### Блок A — подключение боевых ключей (В РАБОТЕ)
- Инструмент: `python -m dirizher.cli.doctor` — проверяет каждый ключ из `.env`;
  `python -m dirizher.cli.doctor yougile-key` — создаёт ключ YouGile из логина/пароля
  и печатает id колонок. Файл `.env` создан (из примера), валидатор пустого
  `TEAM_CHAT_ID` добавлен в `config.py`.
- [ ] `DIRIZHER_TELEGRAM__BOT_TOKEN` (BotFather) + `/setprivacy`→Disable → doctor ✅, добавить бота в тестовый чат
- [ ] `DIRIZHER_LLM__PROVIDER=groq` + `GROQ_API_KEY` (или `gigachat` + creds) → doctor ✅, сверить качество с mock
- [ ] `DIRIZHER_YOUGILE__API_KEY` + ID колонок (`COLUMN_TODO/IN_PROGRESS/DONE`) → doctor ✅, проверить create/move/complete
- [ ] Проверить боевой `YouGileBoard.list_cards` (формат ответа `content[]` — свериться с актуальным API v2)

### Блок B — качество и боевая проверка
- [ ] Прогнать боевой аудио-пайплайн: `pip install -e ".[audio]"`, `DIRIZHER_AUDIO__ENABLED=true`, `HF_TOKEN` для pyannote
- [ ] Связать `SpeakerRegistry` с pyannote-эмбеддингами в `pipeline._diarize` (сейчас отпечатки не извлекаются из аудио — каркас готов, нужен embedding-инференс)
- [ ] Включить `DIRIZHER_MEMORY__BACKEND=chroma` и проверить семантический дедуп («сделать API» ≡ «разработать эндпоинт»)
- [ ] Mock-эвристика слабее на «живых» фразах со встреч (длинные предложения, дедлайн в середине) — это ожидаемо, боевой LLM решает; при желании докрутить `mock_provider._clean_title`/сплит по запятым

### Блок C — не входило в текущий объём (Should/Could из отчёта)
- [ ] Геймификация (очки/уровни/ачивки/лидерборд)
- [ ] Личный кабинет, рекомендации по развитию, общая база знаний
- [ ] Боевой захват системного звука Телемоста (сейчас вход — файл/транскрипт)
- [ ] Персистентность задач в БД (сейчас `TaskRepository` in-memory — за тем же интерфейсом легко заменить)

---

## 🧠 Ключевые решения и почему (чтобы не передумывать зря)

1. **n8n не как код-ядро, а как триггер-слой.** Логика — в тестируемом Python;
   n8n принимает Telegram webhook и cron, форвардит в HTTP-API ядра. Так n8n
   реально используется (требование отчёта), но код остаётся чистым.
2. **Mock-first и `effective_provider`/`is_mock`.** Любой пустой ключ → mock.
   Нельзя ломать офлайн-запуск. Секреты только в `.env`.
3. **Дедуп по умолчанию `lexical`.** ChromaDB на первом старте качает модель ~80МБ —
   это сюрприз-сеть, поэтому `chroma` только явным опт-ином (`DIRIZHER_MEMORY__BACKEND`).
4. **Авто-режим (True/False) per-chat, дефолт False** (с подтверждением) — надёжность.
   Порог `confidence` — отдельный страж: уточняем даже в авто-режиме.
5. **Репозиторий in-memory** — намеренно простой, за интерфейсом; БД добавляется позже.

---

## 🗺️ Карта «где что лежит»

| Нужно… | Файл |
|---|---|
| Поменять конфиг/переменные | `src/dirizher/config.py`, `.env.example` |
| Логику извлечения задач (mock) | `src/dirizher/llm/mock_provider.py` |
| Промпт боевого LLM | `src/dirizher/llm/prompt.py` |
| Дедуп/порог схожести | `src/dirizher/memory/vector_store.py` |
| Действия с доской | `src/dirizher/integrations/yougile.py` |
| Главный конвейер | `src/dirizher/services/task_service.py` |
| Сценарий кнопок/подтверждения | `src/dirizher/bot/flow.py`, `bot/handlers/callbacks.py` |
| Напоминания/вечерняя сверка | `src/dirizher/scheduler/jobs.py`, `services/reconciliation.py` |
| Аудио-пайплайн | `src/dirizher/audio/pipeline.py` |
| HTTP для n8n | `src/dirizher/api/server.py` |
| Сборка зависимостей/состояние | `src/dirizher/container.py` |
| Точка входа | `src/dirizher/main.py` |

---

## ▶️ Как проверять (шпаргалка команд)

```bash
# тесты
PYTHONIOENCODING=utf-8 python -m pytest -q          # 15 passed

# демо цепочки без ключей
PYTHONPATH=src python -m dirizher.cli.simulator
#   you> Максим, сделай авторизацию к четвергу  → y → /board

# демо встречи (.txt: строки "Имя: реплика")
PYTHONPATH=src python -m dirizher.cli.meeting путь/к/transcript.txt

# smoke API
PYTHONPATH=src python -c "from fastapi.testclient import TestClient; \
from dirizher.container import AppContainer; from dirizher.api.server import create_api; \
print(TestClient(create_api(AppContainer())).get('/health').json())"

# боевой запуск (после .env)
python -m dirizher.main
```

### Ожидаемое поведение демо (быстрая самопроверка)
- «Максим, сделай авторизацию к четвергу» → задача, assignee `maxim`, дедлайн ближайший чт, conf ≈ 0.95, outcome `new`.
- Повтор той же фразы после создания → outcome `duplicate` (предложение объединить).
- «нужно поправить баг» (без срока/исполнителя) → outcome `low_confidence` (уточнение).
- `/report ... готово` → статус задачи `done`; `/reconcile` → тег не отписавшихся.

---

## ⚠️ Грабли, на которые уже наступил (не повторять)

- Имя папки проекта **`Вайбуклек`** — кириллица целиком. Дважды опечатался в латиницу
  (`Вайbuклек`) при Write — создавал ложный каталог. **Проверять путь перед записью.**
- ChromaDB **установлен** в среде и при backend=chroma качает ONNX-модель из сети →
  по умолчанию `lexical`.
- aiogram/fastapi/uvicorn/apscheduler уже стоят в текущем окружении.
- `TaskMemory` ловит дубли только среди **созданных** задач (память пополняется в `create_on_board`).
