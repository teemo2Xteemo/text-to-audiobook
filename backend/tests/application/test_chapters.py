from app.application.pipeline.chapters import Chapter, chunk_by_chapter, split_chapters


def test_split_chapters_multilingual():
    text_cn = "序言\n内容\n第一章 启程\n内容一\n第二章 远行\n内容二"
    chapters = split_chapters(text_cn)
    assert len(chapters) == 2
    assert chapters[0].title == "第一章 启程"
    assert chapters[1].title == "第二章 远行"

    text_vn = "Chương 1: Mở đầu\nNội dung 1\nChương 2: Diễn biến\nNội dung 2"
    chapters_vn = split_chapters(text_vn)
    assert len(chapters_vn) == 2

    # Under 2 headings -> single chapter
    text_short = "Chapter 1: The Only One\nContent"
    chapters_single = split_chapters(text_short)
    assert len(chapters_single) == 1
    assert chapters_single[0].title == "Full text"


def test_split_chapters_preface_preserved_in_first_chapter():
    text_cn = "序言\n这是一段前言。\n第一章 启程\n正文开始。\n第二章 结束\n正文结束。"
    chapters = split_chapters(text_cn)
    assert len(chapters) == 2
    assert chapters[0].title == "第一章 启程"
    assert "序言" in chapters[0].body
    assert "这是一段前言。" in chapters[0].body
    assert "正文开始。" in chapters[0].body
    assert chapters[0].char_count == len(chapters[0].body)


def test_split_chapters_english_multichapter():
    text_en = (
        "Chapter 1: The Departure\n\n"
        "It was a dark and stormy night.\n\n"
        "Chapter 2: The Journey\n\n"
        "The road was winding and treacherous.\n\n"
        "Chapter 3: The Arrival\n\n"
        "They reached the gates at dawn."
    )
    chapters = split_chapters(text_en)
    assert len(chapters) == 3
    assert chapters[0].title == "Chapter 1: The Departure"
    assert chapters[0].body == "It was a dark and stormy night."
    assert chapters[1].title == "Chapter 2: The Journey"
    assert chapters[1].body == "The road was winding and treacherous."
    assert chapters[2].title == "Chapter 3: The Arrival"
    assert chapters[2].body == "They reached the gates at dawn."


def test_split_chapters_no_headings_returns_full_text():
    text = "Just a short story with no chapter headings anywhere in it."
    chapters = split_chapters(text)
    assert len(chapters) == 1
    assert chapters[0].index == 1
    assert chapters[0].title == "Full text"
    assert chapters[0].body == text
    assert chapters[0].char_count == len(text)


def test_chunk_by_chapter_sequential_indexing():
    chapters = [
        Chapter(
            index=1,
            title="Chapter 1",
            body="Para 1 of chapter 1.\n\nPara 2 of chapter 1.",
            char_count=45,
        ),
        Chapter(
            index=2,
            title="Chapter 2",
            body="Para 1 of chapter 2.\n\nPara 2 of chapter 2.",
            char_count=45,
        ),
    ]
    # Use max_chars=25 to force each chapter into multiple chunks
    chunks = chunk_by_chapter(chapters, max_chars=25)
    assert len(chunks) == 4
    for i, chunk in enumerate(chunks, start=1):
        assert chunk.index == i
        assert chunk.id == f"chunk-{i:03d}"
    assert chunks[0].text == "Para 1 of chapter 1."
    assert chunks[1].text == "Para 2 of chapter 1."
    assert chunks[2].text == "Para 1 of chapter 2."
    assert chunks[3].text == "Para 2 of chapter 2."


def test_chunk_by_chapter_empty_and_whitespace():
    assert chunk_by_chapter([]) == []
    chapters = [
        Chapter(index=1, title="Empty Chapter", body="   \n\t  ", char_count=0),
    ]
    assert chunk_by_chapter(chapters) == []
