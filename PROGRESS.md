# 🎼 Дирижёр — рабочий статус (для себя)

> Внутренняя памятка: что сделано, что осталось, как всё устроено и как проверять.
> Источник правды по требованиям — отчёт «Этап 1». Архитектурные решения — ниже.
> Последнее обновление: 2026-06-08 (Раунд 8 — авто-имена спикеров реально работают).
> Текущий статус: **113 тестов зелёные**, ключи подключены (telegram/llm/yougile live),
> всё обязательное по кейсу + усиления закрыты. Хронология — по раундам ниже.

## Багфиксы по фидбэку — раунд 3 (персистентность, контекст, мульти-исполнители)
- **Память между перезапусками**: `state_store.py` (`StateStore`) — атомарный JSON
  с командой и задачами; `container._restore_state()` поднимает на старте,
  `container.persist()` пишет после каждого изменения (онбординг, /register,
  авто-регистрация нового участника, создание/правка/статус/удаление задачи).
  Путь `DIRIZHER_MEMORY__STATE_PATH` (по умолчанию `./.data/state.json`, в .gitignore).
- **Контекст/«Создать задачу»**: усилён `llm/prompt.py` (команда-фиксатор ≠ название;
  суть берётся из переписки; не повторять задачи из памяти; несколько людей в одном
  событии → одна задача). Плюс код-страховка `_is_junk_title` в `ingest` отбрасывает
  заголовки-команды («создать задачу», «на доску», «таск»).
- **Мульти-исполнители при правке**: `Task.assignee_yougile_ids: list[str]`;
  `apply_correction` через `_people_in` ставит несколько исполнителей, «добавь к
  исполнителям …» — режим append (`_APPEND_KW`). Карточка назначается на всех
  (`create_card`/`update_card` шлют `assigned: [...]`).
- **/tasks не путает людей**: матч по `yougile_id` (точно), затем по имени; убран
  слив всех открытых задач в чат. Поддержка «таски @username». `BoardCard.assignee_ids`.
- **Канбан точнее**: `list_cards` читает реальную колонку, имена исполнителей и дедлайн.
- **`/forget`** (`/reset_team`): сброс памяти об участниках с подтверждением
  (`TeamRegistry.clear` + `persist`). Задачи не трогаются — они на доске.
- ⚠️ Грабли: первый прогон тестов до изоляции записал тестовых участников в боевой
  `./.data/state.json` (алиас «Данила» у @maxim → неверный исполнитель). Исправлено:
  изоляция `STATE_PATH` в `conftest.py`, файл удалён.
- Тесты: 66 (новые — `test_persistence_assignees.py`, изоляция состояния в `conftest.py`).

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
- #2 Знакомство с привязкой к доске (ГОТОВО, проверено live): кнопка
  «Представиться» → FSM спрашивает <b>email доски YouGile</b> + прозвища
  (`Introduce.waiting_details` в `bot/handlers/onboarding.py`). По email ищем
  пользователя YouGile (`board.find_user_by_email`, GET /users), пишем
  `TeamMember.yougile_id`; при создании карточки ставим `assigned=[yougile_id]`
  (карточка назначается на реального человека). Прозвища → aliases; задачи,
  висевшие на прозвище, переназначаются. `resolve_all` добавлен для тёзок.
- ОСТАЛОСЬ: #6 в полном виде (UI-выбор при двух одинаковых именах без @username —
  сейчас прозвища/почта почти снимают проблему; добить кнопочный выбор позже).

## Токены Groq: ротация ключей + предфильтр + fallback (ГОТОВО, live)
- Дневной лимит Groq (TPD 100k/ключ) выбивался. Решения:
  - **Ротация ключей**: `DIRIZHER_LLM__GROQ_API_KEYS` (через запятую) + основной
    `GROQ_API_KEY`. `config.groq_key_list`, `GroqLLMProvider` крутит ключи при 429,
    запоминает рабочий индекс. Доктор проверяет все ключи.
  - **Fallback**: при недоступности LLM `TaskService` откатывается на mock-эвристику
    (`self._fallback`, флаг `_llm_degraded`) — бот не падает.
  - **Предфильтр** `llm/prefilter.py:looks_taskish` — НЕ зовём LLM на явный мусор
    (приветствия/реакции/эмодзи/короткие вопросы). При любом намёке на задачу —
    зовём (не теряем задачи). Вызывается в `messages.on_text` до `ingest`.
    Окно истории уменьшено 12→10.

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

- **Реализовано ядро MVP целиком + усиления.** Все 6 обязательных пунктов кейса
  закрыты, плюс RPG-геймификация, трекинг нагрузки и авто-имена спикеров по голосу.
- **Тесты: 113 passed.** Система работает и **офлайн** (mock-режим, нулевые
  зависимости для CI), и вживую с подключёнными ключами.
- **Ключи подключены и проверены** доктором: telegram=live `@degree_case_bot`,
  llm=groq, yougile=live, audio=on. Секреты — в `.env` (в .gitignore).
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

### Блок C — Should/Could из отчёта
- [x] Геймификация (очки/уровни/ачивки/лидерборд) — раунд 6, `services/gamification.py`
- [x] Боевой захват системного звука Телемоста — раунд 5, `audio/recorder.py` (loopback)
- [ ] Личный кабинет, рекомендации по развитию, общая база знаний
- [ ] Персистентность задач в БД (сейчас `TaskRepository` in-memory + JSON-снимок — за тем же интерфейсом легко заменить)

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

## Раунд 4 — голосовые и кружки → текст (ГС/video_note)

Цель: бот извлекает текст из голосовых и видео-кружков и прогоняет его через
**тот же** конвейер `c.service.ingest`, что и обычный текст (задачи/исполнители/сроки
извлекаются одинаково). Текстовый бот не тронут.

### Переключатели (config.AudioSettings / .env)
- `DIRIZHER_AUDIO__ENABLED` — **главный рубильник** распознавания. `false` → mock
  (бот просит продублировать текстом; этим же режимом изолированы тесты, нулевые
  зависимости). `true` → включает выбранный бэкенд. **ENABLED=false НЕ переключает
  на faster-whisper** — это именно «выкл»; выбор движка отдельным флагом `BACKEND`.
- `DIRIZHER_AUDIO__BACKEND` — `groq` (по умолчанию) или `local`.
  - `groq` — Whisper через Groq API (`whisper-large-v3-turbo`): облако, без
    ffmpeg/torch/моделей; если свои ключи пусты — берёт ключи `DIRIZHER_LLM__GROQ_*`,
    ротация при 429. **Диаризации нет** — отдаёт плоский текст (для личных ГС это норм).
  - `local` — faster-whisper локально (улучшённый пайплайн с ветки
    `feat/telegram-voice-scanner`): ffmpeg→wav 16k mono → noisereduce → whisper,
    очистка temp, мягкий фолбэк если ffmpeg нет. **С HF-токеном** включается
    pyannote-диаризация и текст размечается `Speaker_1: … / Speaker_2: …`
    (для встреч с несколькими голосами; LLM потом мапит спикеров на имена команды).

### Спикеры (Speaker_1/Speaker_2)
- Это **диаризация**, нужна только для многоголосых записей (встречи), не для личного ГС.
- Работает только на `BACKEND=local` + `DIRIZHER_AUDIO__HF_TOKEN` (модель
  `pyannote/speaker-diarization-3.1`, надо принять её условия на HuggingFace).
- Groq-бэкенд спикеров не различает.
- `WhisperPipeline._join_with_speakers` группирует подряд идущие реплики и
  переименовывает сырые метки pyannote (SPEAKER_00/01) в Speaker_1/2 по порядку.

### Как поставить локальный faster-whisper
```
pip install -e ".[audio]"          # extra audio: faster-whisper, noisereduce, soundfile, pyannote.audio
winget install Gyan.FFmpeg         # ffmpeg в PATH (для конвертации oga→wav и шумоподавления)
```
В `.env`: `DIRIZHER_AUDIO__ENABLED=true`, `DIRIZHER_AUDIO__BACKEND=local`,
`DIRIZHER_AUDIO__WHISPER_MODEL=small` (или `medium`/`large-v3`),
для спикеров — `DIRIZHER_AUDIO__HF_TOKEN=<hf_...>`. Первый запуск качает модель Whisper.

### Файлы
- `audio/groq_transcriber.py` (новый) — `GroqWhisperTranscriber` (ротация ключей).
- `audio/transcriber.py` — `build_transcriber(cfg, fallback_groq_keys)` выбирает бэкенд;
  без ключей → mock (не падаем).
- `audio/pipeline.py` — локальный faster-whisper + опц. диаризация со спикер-разметкой.
- `bot/handlers/voice.py` — ловит `voice | video_note | audio`, статус «Слушаю…»,
  обработка ошибок, очистка temp, регистрация автора (+persist). Голосовое во время
  правки задачи = уточнение.
- `config.AudioSettings` — `backend`, `groq_whisper_model`, `groq_api_keys`, `groq_key_list`.
- Тесты: `test_voice_transcription.py` (выбор бэкенда, ротация, happy-path, спикер-разметка).
  Итого **75 passed**.

### Статус (актуально)
- **Включён локальный режим:** `ENABLED=true`, `BACKEND=local`,
  `WHISPER_MODEL=large-v3-turbo` (≈качество large-v3, но быстрее/легче — лучший выбор для CPU).
- Аудио-пакеты установлены (`pip install -e ".[audio]"`): faster-whisper 1.2.1, torch 2.12+cpu,
  pyannote.audio 4.0, soundfile, av. Модель large-v3-turbo предзагружена в кэш HF.
- `WhisperPipeline._pick_device()` — авто: CUDA(float16) если есть, иначе CPU(int8).
  Сейчас CPU (torch без CUDA) → распознавание идёт на процессоре (медленнее GPU, но работает).
- **ffmpeg в PATH нет** — `_to_wav` пропускается, файл уходит прямо в whisper (декодит через
  PyAV/av, который установлен). Шумоподавление soundfile применяется если читает формат,
  иначе мягко пропускается. Для denoise oga→wav поставить ffmpeg: `winget install Gyan.FFmpeg`.
- Спикеры (Speaker_1/2): `HF_TOKEN` пуст → pyannote-диаризации нет (для личных ГС не нужна).
  **Обновление (Раунд 8):** авто-имена `@username` теперь работают и БЕЗ токена — через
  closed-set идентификацию по реестру голосов (`pipeline._identify_segments`). pyannote
  остаётся опциональным премиум-вариантом для незнакомых голосов.

---

## Раунд 5 — встречи: захват системного звука + голосовые отпечатки

Закрыты два пробела по кейсу: п.3 (захват звука встреч) и привязка голосовых
отпечатков к именам.

### Захват встречи (loopback по ссылке Телемоста)
- Бот **не входит** в Телемост (публичного API нет) — пишет СИСТЕМНЫЙ звук машины,
  которая уже в звонке (захват с драйвера, как в кейсе). Через `soundcard` (WASAPI loopback).
- **Всё автоматизировано:** кто-то кидает в чат `https://telemost.yandex.ru/...` →
  бот сам стартует запись; авто-стоп по тишине (`MEETING_SILENCE_SECONDS`, дефолт 180с)
  или по лимиту (`MEETING_MAX_MINUTES`); ручная страховка — `/meeting_stop`.
- По завершении: WAV → `c.transcriber` (Whisper + диаризация + авто-имена) →
  `MeetingService.process` → саммари в чат + задачи на доску YouGile.
- `audio/recorder.py` — `MeetingRecorder` (фоновый поток, RMS-детект тишины, запись WAV
  через soundfile, async-колбэк через `run_coroutine_threadsafe`). Чистые функции
  `_is_silent`/`_stop_reason` — под юнит-тесты. Проверено вживую: 1.5с → WAV 48КБ → колбэк.
- `bot/handlers/meeting.py` — роутер (до voice/messages): хэндлер ссылки, `/meeting_stop`,
  `/enroll_voice`. `container.active_meetings: dict[chat_id, MeetingRecorder]`; `aclose` глушит запись.

### Голосовые отпечатки (speaker embedding → авто-имя)
- `audio/embeddings.py` — `PyannoteEmbedder` (модель `pyannote/embedding`, лениво):
  `embed_file` (для регистрации) и `embed_turns` (усреднённый эмбеддинг по сегментам
  каждого спикера). `build_embedder` → None без HF-токена/при backend≠local (мягкая деградация).
- `SpeakerRegistry` (`audio/speakers.py`) теперь **подключён**: `WhisperPipeline._resolve_speakers`
  считает эмбеддинги по диаризации → `registry.identify` → имя; неизвестные → `Speaker_N`.
  Один человек = один спикер (имя не переиспользуется в рамках встречи).
- `_join_with_speakers` → разбит на `_resolve_speakers` (raw→имя) + `_join_consecutive` (склейка).
- Регистрация: `/enroll_voice` → FSM `EnrollVoice.waiting_voice` → присылаешь голосовое →
  `embed_file` → `registry.enroll(имя, эмбеддинг)`. Реестр персистентный (`voiceprints.json`).
- Container: `self.speakers`, `self.embedder` прокинуты в `build_transcriber`/пайплайн.

### Зависимости / окружение
- Добавлен `soundcard>=0.4` в extra `[audio]` (поставлен). На этой машине loopback видит
  устройства (Razer/Realtek) — работает.
- ⚠️ Здесь (Раунд 5) авто-имена ещё зависели от `HF_TOKEN`. **Переделано в Раунде 8**:
  добавлен офлайн `SignalEmbedder` (MFCC) + идентификация по сегментам без диаризации,
  декод Opus через PyAV — теперь работает без токена и без ffmpeg в PATH.
- Тесты: `test_meeting_speakers.py` (ссылка-триггер, тишина/таймаут, enroll/identify,
  персистентность отпечатков, авто-имена с фейк-эмбеддером). Итого **82 passed**.

---

## Раунд 6 — геймификация (п.10) + n8n-оркестрация на полный цикл (п.5)

Закрыты последние два пробела по кейсу.

### п.10 — RPG: опыт, уровни, ачивки, лидерборд
- `services/gamification.py` — `GamificationService` + `GameStore` (отдельный
  `./.data/gamification.json`, в .gitignore — там имена). Чистое ядро под тесты:
  `xp_for_completion`, `rank_for`, `_streak_after`, `is_on_time`.
- **Начисление XP**: база 10 + приоритет (high +15 / medium +5) + в срок +10.
  Ранги: Новичок → Боец(50) → Мастер(150) → Эксперт(300) → Гуру(600) → Легенда(1000).
- **Ачивки**: Первая кровь, Пятёрка, Червонец, Полста, Снайпер (5 в срок),
  Пожарный (high), В ударе (серия 3 дня), Неделя огня (7).
- **Идемпотентность**: начисление по `task.id` (`done_task_ids`) — повторное закрытие
  или синк доски не накручивает очки. Мульти-исполнители: XP каждому. Алиасы → один профиль.
- **Подключено ко ВСЕМ точкам «задача→Готово»**: кнопка (`callbacks.on_task_action`),
  карточка «мои задачи» (`on_board_action`), команда в чате (`task_commands`),
  вечерний отчёт (`reconciliation.record_report` — `game` прокинут в сервис).
  После закрытия — короткая (без спама) строка: «🎮 Имя +25 XP · уровень 2 🆙 · 🏆 Ачивка».
- **Команды**: `/profile` (свой или `@user`) с прогресс-баром; `/leaderboard` (`/top`, `/топ`).
- Тесты: `test_gamification.py` (11), `test_jobs_gamification.py` (5).

### п.5 — n8n как оркестратор полного автономного цикла
- Раньше n8n дёргал только напоминания + вечернюю сверку. Добавлены ещё два
  автономных триггера, чтобы цикл «утро→дедлайны→вечер→итоги» шёл через n8n:
  - `run_morning_digest` → `POST /jobs/morning-digest` (утром): задачи на сегодня
    + просрочки по исполнителям.
  - `run_leaderboard_post` → `POST /jobs/leaderboard` (пятница 18:00): игровой лидерборд.
- `api/server.py` — два новых эндпоинта (та же защита `X-Dirizher-Token`).
- `scheduler.py` — те же 4 задания и в APScheduler (локальный паритет с n8n).
- `n8n/dirizher-orchestration.json` — добавлены cron-ноды «утренняя сводка» и
  «лидерборд (пятница)» + HTTP-ноды и связи. Конфиг: `MORNING_DIGEST_CRON`,
  `LEADERBOARD_CRON` в `.env.example`.
- «Нет отчёта → тег» уже жил в `evening_digest` (список не отписавшихся) — оставлен как есть.

Итого **100 passed**.

---

## Раунд 7 — синхронизация с доской, тёзки, чистка лидерборда

Фиксы по фидбэку после боевого использования.

- **«12 задач, а реально 3» — призраки в памяти.** Карточки удаляли на доске
  вручную, но в `state.json` они оставались → бот считал лишнее (ложная нагрузка,
  кривые сводки). Решение — **самосинхронизация память↔доска**:
  `TaskService.reconcile_with_board()` выкидывает задачи, чьих карточек на доске
  уже нет. Запускается на старте бота (`bot/app.py`) и вручную командой **`/sync`**.
  Предохранитель: на mock-доске и при сбое API (доска вернула 0 карточек при
  непустой памяти) ничего не удаляет. Тесты: `test_board_sync.py` (4).
- **«2 Андрея».** У `@Stefan_Richards` (имя «Энди») и `@vaiforic` обоих был алиас
  «Андрей» → `resolve` отдавал первого. Команда **`/alias энди, стеф`** заменяет
  свои прозвища (с предупреждением о коллизии, если тот же алиас есть у другого).
- **Чужие в лидерборде** (`maxim`, `danya` — старые тестовые профили). Команда
  **`/game_reset`** обнуляет лидерборд (`GamificationService.reset`).
- Грамматика: «N дедлайнов» в предупреждении о перегрузке.

Итого **104 passed**.

---

## Раунд 8 — авто-имена спикеров реально заработали (Speaker_1 → @username)

Каркас голосовых отпечатков был, но фича **не работала end-to-end**: голоса
никто не регистрировал по-настоящему, а эмбеддер включался только при
`backend=local` + HF-токен + принятые условия pyannote. На дефолтной установке
`build_embedder` отдавал `None` → спикеры всегда оставались `Speaker_N`.

Сделал так, чтобы работало **без HF-токена и без бинарника ffmpeg**:

- **`SignalEmbedder`** (`audio/embeddings.py`) — голосовой отпечаток на классических
  MFCC (numpy + scipy). Берём средние c1.. (тембр) и СКО (динамика), вектор
  стандартизуем → косинус начинает разделять голоса (свой ~1.0, чужой ~0.58
  на синтетике, было 0.96). `build_embedder` теперь: HF-токен → pyannote (точнее),
  иначе → signal (офлайн, любой бэкенд).
- **`audio/decode.py`** — декодирование Opus `.oga`/`.mp4` через **PyAV** (несёт
  ffmpeg-библиотеки), без бинарника ffmpeg в PATH. WAV — через soundfile.
- **Идентификация через кластеризацию** (`pipeline._identify_segments` + `_cluster`):
  per-segment argmax по MFCC оказался нестабилен (то расщеплял один голос на двоих,
  то склеивал двоих в одного). Переделал: эмбеддинги сегментов **кластеризуются**
  агломеративно (scipy, косинус, порог среза от близости реестра), а реестр голосов
  используется только чтобы **назвать** кластеры. Итог: один голос = один кластер
  (распался — оба назовутся одним именем), двое разных = два кластера. pyannote/
  HF-токен не нужны. Проверено на синтетике: монолог не дробится, два голоса
  размечаются раздельно.
- **`_label_speakers`** теперь показывает **@username** (а не только full_name).
- Снял ложную блокировку `/enroll_voice` (требовала HF-токен) — теперь нужна лишь
  `DIRIZHER_AUDIO__ENABLED=true`. Регистрация из голосового (.oga Opus) проверена:
  декод → MFCC → enroll работают.

Демо (нужен `backend=local`, faster-whisper для сегментов с таймкодами):
каждый участник раз шлёт `/enroll_voice` + голосовое → на встрече реплики
подписываются `@stefan_richard: …` вместо `Speaker_1`.

Тесты: `test_speaker_id.py` (11) — реестр, кластеризация (один голос не дробится,
два размечаются раздельно, over-split назовётся одним именем), разметка встречи в
@username, выбор эмбеддера, реальное разделение голосов SignalEmbedder. Итого **115 passed**.

> ⚠️ На рабочем столе **две копии** проекта: `Vaybuklek` (Latin, с `.git` — рабочая)
> и `Вайбуклек` (Cyrillic, без git, копия от 07.06 — устаревшая). Редактировать и
> запускать строго `Vaybuklek`. Терминал по умолчанию открывается в Cyrillic-папке.

---

## 🎯 Покрытие кейса (обязательный функционал)

| # | Требование кейса | Статус | Где в коде |
|---|---|---|---|
| 1 | Telegram: читает чат, извлекает задачи/дедлайны/ответственных, тег @username | ✅ | `bot/handlers/messages.py`, `llm/`, `repository.TeamRegistry.mention_for` |
| 2 | Встречи: Телемост, захват звука с драйвера, саммари → задачи на доску | ✅ | `audio/recorder.py` (WASAPI loopback), `bot/handlers/meeting.py`, `services/meeting.py` |
| 3 | Канбан: создаёт/двигает/закрывает карточки, статусы | ✅ | `integrations/yougile.py` (`YouGileBoard`), `services/task_service.py` |
| 4 | Проактивные напоминания: «задача через …», «не обновили статус» | ✅ | `scheduler/jobs.run_reminders`, утренняя сводка `run_morning_digest` |
| 5 | Вечерняя синхронизация: сверка отчётов с канбаном, тег не отписавшихся | ✅ | `services/reconciliation.py`, `run_evening_reconciliation` |
| 6 | Минимум ручного управления: бот сам извлекает контекст, только подтверждения | ✅ | `ModeStore` (auto/manual), `flow.present`, предфильтр `llm/prefilter.py` |
| Доп | RPG-геймификация (очки/уровни/ачивки/лидерборд) | ✅ | `services/gamification.py`, `/profile`, `/leaderboard` |
| Доп | Трекинг скорости/качества (в срок, серии, нагрузка) | ✅ | `gamification` (on_time/streak), `workload_warning` |
| Доп | Голосовые отпечатки → авто-имена спикеров (`Speaker_1`→`@username`) | ✅ работает офлайн | `audio/embeddings.SignalEmbedder`, `audio/decode.py` (PyAV), `pipeline._identify_segments`, `audio/speakers.py`, `/enroll_voice` — без HF-токена (Раунд 8) |
| Доп | Личный кабинет / рекомендации / база знаний | ⛔ | не делали (за рамками 3-4 дней) |

Из архитектурного отчёта (Документ Word) реализовано: webhook (1), LLM-извлечение (2),
захват звука встреч (3), ГС/кружки (4), n8n-оркестратор (5), ChromaDB (6), MD-снимок (7),
YouGile (8), Telegram-действия (9), RPG (10), n8n Scheduler — утро/дедлайны/вечер/нет-отчёта (11).

## 🤖 Как работает n8n-оркестрация (п.5/11) — что автоматизировано

n8n — это «внешние часы и роутер». Само ядро (извлечение задач, доска, память) —
это сервисы Дирижёра; n8n лишь даёт им стабильные точки входа по HTTP и дёргает по
расписанию. Флоу: `n8n/dirizher-orchestration.json` (импортируется в n8n).

**Автоматические триггеры (полный суточный цикл):**
- **Webhook** Telegram → `POST /ingest/telegram` — каждое сообщение из чата уходит в ядро.
- **Утро 09:00** → `POST /jobs/morning-digest` — «задачи на сегодня + просрочки» по людям.
- **10:00 и 15:00** → `POST /jobs/reminders` — близкие/просроченные дедлайны, тег @username.
- **Вечер 20:00** → `POST /jobs/evening-reconcile` — сверка отчётов с доской; кто не
  отписался — тегается в чате.
- **Пятница 18:00** → `POST /jobs/leaderboard` — игровой лидерборд недели.

Все эндпоинты защищены `X-Dirizher-Token`. Те же задания продублированы локально в
APScheduler (`scheduler/scheduler.py`), поэтому бот автономен **и без** n8n — n8n нужен,
когда оркестрацию хотят вынести наружу (как в отчёте). «Вечером отчёт по дню» — да, это
`evening-reconcile`; «утром напоминание» — да, `morning-digest` + `reminders`.

---

## ⚠️ Грабли, на которые уже наступил (не повторять)

- **Две копии проекта на рабочем столе:** `Vaybuklek` (латиница, с `.git` — **рабочая**,
  все Раунды 6–8 тут) и `Вайбуклек` (кириллица, без git, копия от 07.06 — **устаревшая**).
  Терминал по умолчанию открывается в кириллической → `Grep`/`Glob` без явного пути бьют
  по старой копии и врут. **Всегда явный путь `…/Vaybuklek/…`; кириллическую копию удалить.**
- ⚠️ Декод аудио из Telegram (Opus `.oga`, кружки `.mp4`) — через **PyAV** (`audio/decode.py`),
  бинарник ffmpeg в PATH НЕ нужен. PyAV умеет decode/encode opus/aac; vorbis-энкодера в сборке нет.
- ⚠️ **Пути данных были относительными (`./.data/...`) → зависели от cwd запуска.** Бот,
  запущенный из кириллической копии (cwd терминала по умолчанию), читал пустой
  `Вайбуклек/.data` → реестр голосов пуст → встреча БЕЗ меток спикеров (хотя голоса
  записаны в `Vaybuklek/.data`). Починено: в `config.py` пути привязаны к корню проекта
  (`_ROOT = parents[2]`, `_data(...)`) — теперь cwd запуска не важен. (Раунд 8.1)
- ChromaDB **установлен** в среде и при backend=chroma качает ONNX-модель из сети →
  по умолчанию `lexical`.
- aiogram/fastapi/uvicorn/apscheduler уже стоят в текущем окружении.
- `TaskMemory` ловит дубли только среди **созданных** задач (память пополняется в `create_on_board`).

---

## ▶️ Чек-лист демо (по сценариям кейса)

**Перед показом:** `py -m dirizher.main` (из `Vaybuklek`), запущен polling + API :8080.
Прогнать `/sync` (убрать призраков), `/game_reset` (чистый лидерборд), с аккаунта SR
`/alias энди, стеф` (развести Андреев).

1. **Чат → задача.** Написать «Стеф, сделай лабу к пятнице» → карточка в YouGile + тег.
2. **Голосовое → задача.** Прислать ГС с задачей → молча появляется карточка.
3. **Закрытие → XP.** Закрыть задачу → «🎮 +25 XP · уровень 2 🆙». Затем `/leaderboard`.
4. **Авто-имена спикеров** (нужен `BACKEND=local`): один раз каждый шлёт `/enroll_voice`
   + голосовое → на встрече реплики подписаны `@stefan_richard: …` вместо `Speaker_1`.
5. **Встреча.** Кинуть ссылку Телемоста → запись → саммари + задачи на доску.
6. **Вечер.** `/report сделал X` → статусы на доске; `/reconcile` — сводка + тег молчунов.

## 🧭 Дальнейшие шаги (вне текущего объёма)

- **Посегментные таймкоды в Groq-путь** (`response_format=verbose_json`) — тогда авто-имена
  спикеров заработают и на облачном бэкенде, без локальной faster-whisper.
- Личный кабинет, рекомендации по развитию, общая база знаний (из отчёта Won't-have).
- UI-выбор исполнителя при полных тёзках без @username (сейчас снимается алиасами/почтой).
- Поддержка Zoom/Teams (архитектура захвата звука уже абстрагирована).

## 🗂️ Карта модулей (быстрый ориентир)

| Слой | Модули |
|---|---|
| Вход (Telegram) | `bot/handlers/{messages,voice,meeting,commands,callbacks,onboarding}.py`, `bot/flow.py` |
| Извлечение задач | `llm/{extractor,prompt,parsing,prefilter}.py`, `services/task_service.py` |
| Аудио/речь | `audio/{transcriber,groq_transcriber,pipeline,recorder,embeddings,speakers,decode}.py` |
| Доска/память | `integrations/yougile.py`, `memory/{vector_store,project_snapshot}.py`, `state_store.py` |
| Автономный цикл | `scheduler/{scheduler,jobs}.py`, `services/reconciliation.py`, `api/server.py`, `n8n/` |
| Геймификация | `services/gamification.py` |
| Сборка/конфиг | `container.py`, `config.py`, `main.py` |
