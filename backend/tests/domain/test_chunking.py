from app.domain.chunking import CHUNK_MAX_CHARS, chunk_text


def test_empty_text_yields_no_chunks() -> None:
    assert chunk_text("") == []


def test_short_mixed_script_stays_one_chunk() -> None:
    text = "你好, Hello, مرحبا"
    chunks = chunk_text(text)
    assert len(chunks) == 1
    assert chunks[0].id == "chunk-001"
    assert chunks[0].index == 1
    assert chunks[0].text == text


def test_paragraphs_that_fit_pack_together() -> None:
    text = "First paragraph.\n\nSecond paragraph."
    chunks = chunk_text(text, max_chars=1200)
    assert len(chunks) == 1
    assert chunks[0].text == text


def test_mixed_script_long_input_yields_stable_ids() -> None:
    chinese = "汉" * 800
    latin = "A" * 800
    text = f"{chinese}\n\n{latin}"
    chunks = chunk_text(text, max_chars=1200)
    assert len(chunks) >= 2
    assert all(len(chunk.text) <= 1200 for chunk in chunks)
    assert [chunk.id for chunk in chunks] == [f"chunk-{i:03d}" for i in range(1, len(chunks) + 1)]
    again = chunk_text(text, max_chars=1200)
    assert [chunk.id for chunk in again] == [chunk.id for chunk in chunks]
    assert [chunk.text for chunk in again] == [chunk.text for chunk in chunks]


def test_cjk_sentence_terminals_pack_without_english_only_split() -> None:
    sentence = "你好。世界！"
    text = sentence * 80  # 480 chars, well under default
    chunks = chunk_text(text)
    assert len(chunks) == 1
    oversized = ("你好。世界！") * 300  # 1800 chars
    split = chunk_text(oversized, max_chars=1200)
    assert len(split) >= 2
    assert all(len(chunk.text) <= 1200 for chunk in split)
    assert "".join(chunk.text for chunk in split) == oversized


def test_oversize_sentence_is_hard_split_by_character() -> None:
    text = "A" * 2500
    chunks = chunk_text(text, max_chars=1200)
    assert [chunk.id for chunk in chunks] == ["chunk-001", "chunk-002", "chunk-003"]
    assert chunks[0].text == "A" * 1200
    assert chunks[1].text == "A" * 1200
    assert chunks[2].text == "A" * 100
    assert all(len(chunk.text) <= 1200 for chunk in chunks)


def test_default_budget_is_domain_constant() -> None:
    assert CHUNK_MAX_CHARS == 1200
    text = "x" * 1201
    chunks = chunk_text(text)
    assert all(len(chunk.text) <= CHUNK_MAX_CHARS for chunk in chunks)
    assert len(chunks) == 2
