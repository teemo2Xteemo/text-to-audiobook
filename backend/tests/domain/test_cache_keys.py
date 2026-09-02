from app.domain.audio import TTSSettings
from app.domain.cache import build_cache_key


def _key(**overrides: object) -> str:
    payload: dict[str, object] = {
        "operation": "tts",
        "text": "hello",
        "source_language": "zh-CN",
        "target_language": "vi-VN",
        "provider": "fake",
        "model": "fake-1",
        "voice": "voice-a",
        "settings": TTSSettings(speed=1.0),
    }
    payload.update(overrides)
    return build_cache_key(
        operation=str(payload["operation"]),
        text=str(payload["text"]),
        source_language=str(payload["source_language"]),
        target_language=str(payload["target_language"]),
        provider=str(payload["provider"]),
        model=str(payload["model"]),
        voice=str(payload["voice"]),
        settings=payload["settings"],  # type: ignore[arg-type]
    )


def test_same_inputs_produce_same_key() -> None:
    assert _key() == _key()


def test_settings_dict_order_does_not_change_key() -> None:
    left = _key(settings={"speed": 1.0, "format": "mp3"})
    right = _key(settings={"format": "mp3", "speed": 1.0})
    assert left == right


def test_target_language_change_is_cache_miss() -> None:
    assert _key(target_language="vi-VN") != _key(target_language="en-US")


def test_voice_change_is_cache_miss() -> None:
    assert _key(voice="voice-a") != _key(voice="voice-b")


def test_source_language_change_is_cache_miss() -> None:
    assert _key(source_language="zh-CN") != _key(source_language="ja-JP")


def test_speed_change_is_cache_miss() -> None:
    assert _key(settings=TTSSettings(speed=1.0)) != _key(settings=TTSSettings(speed=1.25))


def test_operation_separates_translation_and_tts() -> None:
    translation = _key(operation="translation", voice="")
    tts = _key(operation="tts", voice="voice-a")
    assert translation != tts
