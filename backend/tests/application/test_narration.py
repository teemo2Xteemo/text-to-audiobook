from app.application.pipeline.conservative_narration import ConservativeNarrationProcessor

LATIN_RUN_ON = 'The door opened!!! "Wait..." she whispered. Then 123 cats & Dr. Smith left.'
CJK_TERMINALS = "こんにちは。世界！次へ…"
HANGUL_SENTENCES = "문이 열렸다. 그는 말했다."
CURLY_DIALOGUE = "She whispered, “Wait.” Then she left."


def _alnum_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    buf: list[str] = []
    for char in text:
        if char.isalnum():
            buf.append(char)
        elif buf:
            tokens.append("".join(buf))
            buf = []
    if buf:
        tokens.append("".join(buf))
    return tokens


def test_empty_and_whitespace_return_empty() -> None:
    processor = ConservativeNarrationProcessor()
    assert processor.process("", "en-US") == ""
    assert processor.process("   \n\t  ", "en-US") == ""


def test_language_does_not_change_output() -> None:
    processor = ConservativeNarrationProcessor()
    assert processor.process(LATIN_RUN_ON, "en-US") == processor.process(LATIN_RUN_ON, "ko-KR")


def test_latin_punctuation_gains_breaks_and_keeps_clauses() -> None:
    processor = ConservativeNarrationProcessor()
    narrated = processor.process(LATIN_RUN_ON, "en-US")
    assert narrated != LATIN_RUN_ON
    assert "\n\n" in narrated
    assert "..." in narrated
    assert "!!!" not in narrated
    assert "123" in narrated
    assert "&" in narrated
    assert "Dr." in narrated or "Dr" in narrated
    assert _alnum_tokens(LATIN_RUN_ON) == _alnum_tokens(narrated)


def test_cjk_sentence_terminals_become_blocks() -> None:
    processor = ConservativeNarrationProcessor()
    narrated = processor.process(CJK_TERMINALS, "en-US")
    assert narrated != CJK_TERMINALS
    assert "\n\n" in narrated
    assert "..." in narrated
    assert "…" not in narrated
    assert _alnum_tokens(CJK_TERMINALS) == _alnum_tokens(narrated)


def test_hangul_sentences_become_blocks() -> None:
    processor = ConservativeNarrationProcessor()
    narrated = processor.process(HANGUL_SENTENCES, "ko-KR")
    assert narrated == "문이 열렸다.\n\n그는 말했다."
    assert _alnum_tokens(HANGUL_SENTENCES) == _alnum_tokens(narrated)


def test_curly_quotes_straighten_and_quoted_sentence_is_own_block() -> None:
    processor = ConservativeNarrationProcessor()
    narrated = processor.process(CURLY_DIALOGUE, "en-US")
    assert "“" not in narrated
    assert "”" not in narrated
    assert '"Wait."' in narrated
    blocks = narrated.split("\n\n")
    assert any(block == '"Wait."' or block.endswith('"Wait."') for block in blocks)
    assert _alnum_tokens(CURLY_DIALOGUE) == _alnum_tokens(narrated)


def test_dashes_and_mixed_terminals_collapse() -> None:
    processor = ConservativeNarrationProcessor()
    source = "Hello—world!?! Go now??"
    narrated = processor.process(source, "en-US")
    assert "—" in narrated
    assert "!?" not in narrated
    assert "??" not in narrated
    assert _alnum_tokens(source) == _alnum_tokens(narrated)
