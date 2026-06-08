"""Тесты записи встреч и голосовых отпечатков (чистая логика, без аудио-железа)."""

from __future__ import annotations

from dirizher.audio.recorder import _is_silent, _stop_reason
from dirizher.audio.speakers import SpeakerRegistry
from dirizher.bot.handlers.meeting import has_telemost_link


# ── Ссылка Телемоста — триггер записи ────────────────────────────────────────
def test_telemost_link_detected():
    assert has_telemost_link("го на созвон https://telemost.yandex.ru/j/18780842719093")
    assert has_telemost_link("https://telemost.yandex.kz/j/123")
    assert not has_telemost_link("https://zoom.us/j/123")
    assert not has_telemost_link("просто текст без ссылки")


# ── Авто-стоп записи: тишина и таймаут ───────────────────────────────────────
def test_silence_threshold():
    assert _is_silent(0.001, 0.004) is True
    assert _is_silent(0.02, 0.004) is False


def test_stop_reason_silence_and_timeout():
    # тишина набралась → стоп по тишине
    assert _stop_reason(180, 600, silence_limit=180, max_s=10800) == "silence"
    # ещё не натишину и не предел → продолжаем
    assert _stop_reason(10, 600, silence_limit=180, max_s=10800) is None
    # достигнут предел длительности → стоп
    assert _stop_reason(0, 10800, silence_limit=180, max_s=10800) == "timeout"
    # silence_limit=0 отключает авто-стоп по тишине
    assert _stop_reason(9999, 100, silence_limit=0, max_s=10800) is None


# ── Голосовые отпечатки: enroll/identify ─────────────────────────────────────
def test_voiceprint_enroll_and_identify(tmp_path):
    reg = SpeakerRegistry(str(tmp_path / "vp.json"), threshold=0.9)
    reg.enroll("Данила", [1.0, 0.0, 0.0])
    reg.enroll("Андрей", [0.0, 1.0, 0.0])
    # близкий к «Данила» вектор → Данила
    assert reg.identify([0.98, 0.02, 0.0]) == "Данила"
    # ортогональный — никого выше порога
    assert reg.identify([0.0, 0.0, 1.0]) is None


def test_voiceprint_persists_across_restart(tmp_path):
    path = str(tmp_path / "vp.json")
    SpeakerRegistry(path).enroll("Стефан", [0.3, 0.7])
    reborn = SpeakerRegistry(path, threshold=0.9)
    assert reborn.identify([0.31, 0.69]) == "Стефан"


# ── Диаризация → авто-имена по реестру (с фейк-эмбеддером) ────────────────────
def test_pipeline_resolves_known_speaker_by_voice(tmp_path):
    from dirizher.audio.pipeline import WhisperPipeline
    from dirizher.config import AudioSettings

    reg = SpeakerRegistry(str(tmp_path / "vp.json"), threshold=0.9)
    reg.enroll("Данила", [1.0, 0.0])

    class FakeEmbedder:
        def embed_file(self, p):
            return [1.0, 0.0]

        def embed_turns(self, wav, turns):
            # SPEAKER_00 — это Данила, SPEAKER_01 — неизвестный
            return {"SPEAKER_00": [0.99, 0.01], "SPEAKER_01": [0.0, 1.0]}

    pipe = WhisperPipeline(AudioSettings(), speaker_registry=reg, embedder=FakeEmbedder())
    turns = [(0.0, 2.0, "SPEAKER_00"), (2.0, 4.0, "SPEAKER_01")]
    label_map = pipe._resolve_speakers("ignored.wav", turns)
    assert label_map["SPEAKER_00"] == "Данила"  # узнан по голосу
    assert label_map["SPEAKER_01"] == "Speaker_1"  # неизвестный → аноним


def test_pipeline_anonymous_without_embedder(tmp_path):
    from dirizher.audio.pipeline import WhisperPipeline
    from dirizher.config import AudioSettings

    pipe = WhisperPipeline(AudioSettings())  # без реестра/эмбеддера
    turns = [(0.0, 2.0, "SPEAKER_00"), (2.0, 4.0, "SPEAKER_01"), (4.0, 5.0, "SPEAKER_00")]
    label_map = pipe._resolve_speakers("x.wav", turns)
    assert label_map == {"SPEAKER_00": "Speaker_1", "SPEAKER_01": "Speaker_2"}
