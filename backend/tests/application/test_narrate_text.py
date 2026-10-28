def test_prepare_narration_text_cleanup():
    from app.application.pipeline.parse import prepare_narration_text

    raw = "　　Line 1   \r\n\r\n* * *\r\n\r\n\r\nLine 2\n---\nLine 3"
    cleaned = prepare_narration_text(raw)
    assert "　　" not in cleaned
    assert "* * *" not in cleaned
    assert "---" not in cleaned
    assert "\r" not in cleaned
    assert "\n\n\n" not in cleaned


def test_prepare_narration_text_empty_and_whitespace():
    from app.application.pipeline.parse import prepare_narration_text

    assert prepare_narration_text("") == ""
    assert prepare_narration_text("   \n\r\n\t  ") == ""


def test_prepare_narration_text_dividers():
    from app.application.pipeline.parse import prepare_narration_text

    raw = "Heading\n\n===\n\nBody\n\n- - -\n\nEnd"
    cleaned = prepare_narration_text(raw)
    assert "===" not in cleaned
    assert "- - -" not in cleaned
    assert "Heading\n\nBody\n\nEnd" == cleaned
