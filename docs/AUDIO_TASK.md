# 🎙️ Бриф: аудио (голосовые + кружки) — для второго разработчика

Привет! Ты берёшь на себя **аудио-ветку** проекта «Дирижёр»: распознавание
голосовых сообщений и видео-кружков в Telegram (и записей встреч). Параллельно
второй человек добивает **контекст** (когда и какие задачи заводить). Этот файл —
чтобы ты въехал за 15 минут и мы не мешали друг другу.

---

## 1. Что такое «Дирижёр» (коротко)

Автономный AI-проджект-менеджер для Telegram: читает чат/встречи → LLM извлекает
задачи (строгий JSON + confidence) → подтверждение → карточки в YouGile →
напоминания и вечерняя сверка. Принцип **mock-first**: если ключа/зависимости нет,
компонент уходит в mock, и всё запускается офлайн. Подробности — в [README.md](../README.md)
и [PROGRESS.md](../PROGRESS.md) (там же раздел «Что осталось»).

Стек: Python 3.11+, aiogram 3, APScheduler, FastAPI, pydantic. Ядро чистое и
тестируемое; n8n — внешний слой триггеров.

---

## 2. Установка (с нуля)

```bash
git clone https://github.com/StefanWoolf/Vaybuklek.git
cd Vaybuklek
python -m venv .venv
.venv\Scripts\activate                 # Windows (PowerShell: .venv\Scripts\Activate.ps1)
pip install -e ".[dev,audio]"          # ядро + тесты + аудио-зависимости
copy .env.example .env                 # свой конфиг (можно оставить пустым = mock)
```

Проверка, что всё живо:
```bash
pytest -q                              # должно быть 15 passed
python -m dirizher.cli.simulator       # демо «чат → задача → доска» без сети
```

### Что нужно именно для аудио
- **ffmpeg** в PATH (декодирование .oga/.mp4):
  `winget install Gyan.FFmpeg`  (или choco/scoop), проверь `ffmpeg -version`.
- **HF-токен** для диаризации pyannote: зарегистрируйся на huggingface.co,
  прими условия модели `pyannote/speaker-diarization-3.1`, создай токен.
- В `.env`:
  ```
  DIRIZHER_AUDIO__ENABLED=true
  DIRIZHER_AUDIO__WHISPER_MODEL=small        # tiny/base/small/medium/large-v3
  DIRIZHER_AUDIO__HF_TOKEN=hf_...            # для pyannote (диаризация)
  DIRIZHER_AUDIO__GROQ_API_KEY=              # опц.: если делать STT через Groq Whisper API
  ```
- Свой Telegram-бот для живого теста (по желанию): `DIRIZHER_TELEGRAM__BOT_TOKEN`
  от @BotFather + `/setprivacy → Disable`. Без него — тестируй через CLI (ниже).

> Ключей боевых сервисов в репозитории НЕТ (`.env` в .gitignore). У тебя свой `.env`.

---

## 3. Где лежит твой код (карта аудио-слоя)

| Файл | Что это | Статус |
|---|---|---|
| `src/dirizher/audio/transcriber.py` | Протокол `Transcriber`, `TranscriptResult`/`Segment`, `MockTranscriber`, `build_transcriber()` | каркас готов |
| `src/dirizher/audio/pipeline.py` | `WhisperPipeline`: noisereduce → pyannote → faster-whisper | **черновик, доделывать** |
| `src/dirizher/audio/speakers.py` | `SpeakerRegistry`: голосовые отпечатки enroll/identify (JSON) | готов, но **не подключён к пайплайну** |
| `src/dirizher/services/meeting.py` | `MeetingService`: транскрипт → саммари → задачи | готов |
| `src/dirizher/bot/handlers/voice.py` | Хендлер голосовых/кружков в Telegram | каркас (в mock честно деградирует) |
| `src/dirizher/cli/meeting.py` | CLI: обработать .wav/.txt запись встречи | готов |
| `src/dirizher/config.py` → `AudioSettings` | Настройки аудио | трогать можно (только эту секцию) |

Как сейчас работает поток: голосовое/кружок → `voice.py` скачивает файл →
`container.transcriber.transcribe(path)` → `TranscriptResult` → дальше как обычный
текст (`service.ingest`) или как правка задачи. В mock-режиме транскрайбер
возвращает пустой текст и бот вежливо просит продублировать текстом.

---

## 4. Твоя задача (Definition of Done)

Цель: **реальное распознавание ГС и кружков**, чтобы из голосового рождалась задача.

Подзадачи (примерно по приоритету):
1. **Декодирование входа.** Telegram шлёт голосовые как `.oga` (opus), кружки —
   `.mp4`. Убедись, что `WhisperPipeline` их читает (через ffmpeg). При необходимости
   конвертируй в wav во временный файл.
2. **STT.** Доведи `pipeline._whisper_segments` на `faster-whisper` (язык ru,
   `vad_filter=True`). Опционально добавь альтернативу — **Groq Whisper API**
   (быстро, без локальной модели) и переключатель в `AudioSettings`
   (`DIRIZHER_AUDIO__GROQ_API_KEY` уже есть в конфиге).
3. **Шумоподавление.** Проверь `_denoise` (noisereduce) на реальных ГС — даёт ли
   прирост, не ломает ли короткие записи. Сделай безопасным (try/except, как сейчас).
4. **Диаризация + спикеры.** `_diarize` (pyannote) уже размечает Speaker_N.
   Свяжи это со `SpeakerRegistry`: на первой встрече/регистрации сохраняем
   голосовой отпечаток (embedding), потом мэпим Speaker_N → имя без LLM
   (механика из отчёта 3.3). Эмбеддинги pyannote умеет отдавать — это сейчас
   главный «пробел».
5. **Бот UX.** В `voice.py`: понятные ответы («🎙️ Распознал: …»), обработка
   ошибок, и правка задачи голосом (этот путь уже есть — проверь на реале).
6. **Тест.** Добавь `tests/test_audio.py` (можно с маленьким wav-фикстуром или
   мокая faster-whisper), чтобы CI был зелёным и без тяжёлых загрузок по умолчанию.

**DoD:** отправляешь боту голосовое «Максим, сделай авторизацию к четвергу» →
бот транскрибирует → извлекает задачу → показывает карточку → после ✅ создаётся
в YouGile. Плюс `python -m dirizher.cli.meeting запись.wav` выдаёт саммари и задачи.

Как тестировать без бота:
```bash
python -m dirizher.cli.meeting путь/к/записи.wav     # боевой STT (audio enabled)
python -m dirizher.cli.meeting транскрипт.txt        # строки «Имя: реплика», без аудио
```

---

## 5. ⚠️ Контракты — НЕ меняй без согласования

Эти интерфейсы держат наши две ветки совместимыми. Если надо менять — пишем друг
другу и правим вместе.

- `Transcriber.transcribe(file_path: str) -> TranscriptResult` (async) — сигнатура.
- `TranscriptResult(text: str, segments: list[Segment], is_mock: bool)` и
  `Segment(speaker: str, text: str)` — поля. Можешь ДОБАВЛЯТЬ поля (с дефолтами),
  но не переименовывай существующие.
- `MeetingService.process(transcript, *, chat_id=None, today=None)` — вход/выход.
- `build_transcriber(cfg: AudioSettings) -> Transcriber` — фабрика и mock-фолбэк
  обязаны остаться (офлайн-запуск не должен ломаться).
- `TaskService.ingest(text, source, *, today=None, history=None)` — это территория
  контекста (второй разработчик). Голосовой путь должен звать `ingest` так же, как
  текстовый (с `history=c.history.recent(chat_id)`), чтобы «поставь таску» голосом
  тоже работало. Если нужно расширить ingest — согласуй.

---

## 6. Как не мешать друг другу (раздел файлов)

**Твоё (аудио) — правишь свободно:**
`src/dirizher/audio/*`, `bot/handlers/voice.py`, `cli/meeting.py`,
`services/meeting.py`, секция `AudioSettings` в `config.py`, `tests/test_audio.py`.

**Контекст (второй разработчик) — не трогай без спроса:**
`chat_history.py`, `llm/prompt.py`, `llm/mock_provider.py`,
`services/task_service.py` (ingest/классификация), `bot/handlers/messages.py`.

**Общее — мелкими правками и с предупреждением:**
`config.py`, `container.py`, `domain/*`, `bot/flow.py`, `README.md`, `PROGRESS.md`.

### Git-процесс
```bash
git checkout main && git pull          # всегда начинай с свежего main
git checkout -b feat/audio-stt         # своя ветка (у второго — feat/context)
# ... работаешь, коммитишь маленькими порциями ...
git push -u origin feat/audio-stt      # пушишь ветку, открываешь Pull Request
```
- Работаем в **отдельных ветках** + PR в `main` (не пушим напрямую в main).
- Перед началом дня — `git pull` из main; держим PR небольшими.
- Конфликт почти исключён, если каждый сидит в своих файлах (раздел выше).
- Можно завести `develop` как общую интеграционную ветку — на ваше усмотрение.

---

## 7. Стиль кода (чтобы ревью было быстрым)

- Комментарии и тексты для пользователя — на русском, как в существующем коде.
- Тяжёлые зависимости (faster-whisper, pyannote, noisereduce) импортируем **лениво**
  внутри методов, а не на уровне модуля (см. `pipeline.py`) — чтобы ядро и тесты
  не тянули их без надобности.
- Не ломай mock-режим: при `DIRIZHER_AUDIO__ENABLED=false` всё должно стартовать.
- Никаких секретов в коде/коммитах — только через `.env`.
- Перед PR: `pytest -q` зелёный.

Вопросы по архитектуре — `README.md` (раздел «Архитектура») и `PROGRESS.md`.
Удачи, погнали! 🎼
