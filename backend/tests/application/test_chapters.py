import pytest

from app.application.pipeline.chapters import (
    Chapter,
    chunk_by_chapter,
    is_chapter_heading,
    split_chapters,
)


def test_split_chapters_multilingual():
    text_cn = "序言\n内容\n第一章 启程\n内容一\n第二章 远行\n内容二"
    chapters = split_chapters(text_cn)
    # Preface is chapter 1, followed by Chapter 1 and Chapter 2
    assert len(chapters) == 3
    assert chapters[0].title == ""
    assert "序言" in chapters[0].body
    assert chapters[1].title == "第一章 启程"
    assert chapters[2].title == "第二章 远行"

    text_vn = "Chương 1: Mở đầu\nNội dung 1\nChương 2: Diễn biến\nNội dung 2"
    chapters_vn = split_chapters(text_vn)
    assert len(chapters_vn) == 2

    # Single heading preserves chapter title
    text_short = "Chapter 1: The Only One\nContent"
    chapters_single = split_chapters(text_short)
    assert len(chapters_single) == 1
    assert chapters_single[0].title == "Chapter 1: The Only One"
    assert chapters_single[0].body == "Content"


def test_split_chapters_preface_emitted_before_first_chapter():
    text_cn = "序言\n这是一段前言。\n第一章 启程\n正文开始。\n第二章 结束\n正文结束。"
    chapters = split_chapters(text_cn)
    assert len(chapters) == 3
    # Preface has index=0, title="" so Chapter 1 stays index=1
    assert chapters[0].index == 0
    assert chapters[0].title == ""
    assert "序言\n这是一段前言。" in chapters[0].body

    assert chapters[1].index == 1
    assert chapters[1].title == "第一章 启程"
    assert chapters[1].body == "正文开始。"

    assert chapters[2].index == 2
    assert chapters[2].title == "第二章 结束"
    assert chapters[2].body == "正文结束。"


def test_split_chapters_chinese_no_space_and_bracket_headings():
    # N1: Scraped web-novels often omit spaces between chapter number and title,
    # or use quotes/brackets such as 「」, 【】, 《》
    text = (
        "第一章天才少年\n这是天才少年的故事。\n\n"
        "第1章「开端」\n这是开端的故事。\n\n"
        "第3章【突破】\n这是突破的故事。\n\n"
        "第4章《重逢》\n这是重逢的故事。"
    )
    chapters = split_chapters(text)
    assert len(chapters) == 4
    assert chapters[0].index == 1
    assert chapters[0].title == "第一章天才少年"
    assert chapters[0].body == "这是天才少年的故事。"

    assert chapters[1].index == 2
    assert chapters[1].title == "第1章「开端」"
    assert chapters[1].body == "这是开端的故事。"

    assert chapters[2].index == 3
    assert chapters[2].title == "第3章【突破】"
    assert chapters[2].body == "这是突破的故事。"

    assert chapters[3].index == 4
    assert chapters[3].title == "第4章《重逢》"
    assert chapters[3].body == "这是重逢的故事。"


def test_split_chapters_prose_sentences_not_treated_as_headings():
    # N2: Sentences containing clause/dialogue punctuation or starting as prose
    # must not false-trigger chapter heading detection.
    text_vi = "Hồi 3 tuổi, tôi ở quê.\nĐó là một mùa hè đáng nhớ."
    chapters_vi = split_chapters(text_vi)
    assert len(chapters_vi) == 1
    assert chapters_vi[0].title == "Full text"

    text_en = "Chapter 3 was the best part, he said.\nEveryone in the room agreed."
    chapters_en = split_chapters(text_en)
    assert len(chapters_en) == 1
    assert chapters_en[0].title == "Full text"

    text_zh_class = "第一节课，老师走进了教室，开始了新学期的课程。\n大家认真听讲。"
    chapters_zh = split_chapters(text_zh_class)
    assert len(chapters_zh) == 1
    assert chapters_zh[0].title == "Full text"


def test_split_chapters_chinese_action_prose_does_not_split():
    # Combat prose starting with '第三回合' should NOT match as a chapter heading
    text = "第三回合，他一拳打向了对手的胸口，将对方震退了三步。\n接下来的战斗更加激烈。"
    chapters = split_chapters(text)
    assert len(chapters) == 1
    assert chapters[0].title == "Full text"
    assert chapters[0].body == text


def test_split_chapters_chinese_full_width_and_standalone_headings():
    # Full-width digits and standalone headings
    text = "第１章 开端\n这里是开端内容。\n\n第十二章\n这里是第十二章内容。"
    chapters = split_chapters(text)
    assert len(chapters) == 2
    assert chapters[0].title == "第１章 开端"
    assert chapters[0].body == "这里是开端内容。"
    assert chapters[1].title == "第十二章"
    assert chapters[1].body == "这里是第十二章内容。"


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
    # Use max_chars=25 to force each chapter paragraph into separate chunks
    chunks = chunk_by_chapter(chapters, max_chars=25)
    assert len(chunks) == 6
    for i, chunk in enumerate(chunks, start=1):
        assert chunk.index == i
        assert chunk.id == f"chunk-{i:03d}"
    assert chunks[0].text == "Chapter 1"
    assert chunks[1].text == "Para 1 of chapter 1."
    assert chunks[2].text == "Para 2 of chapter 1."
    assert chunks[3].text == "Chapter 2"
    assert chunks[4].text == "Para 1 of chapter 2."
    assert chunks[5].text == "Para 2 of chapter 2."


def test_chunk_by_chapter_forces_boundary_between_chapters():
    chapters = [
        Chapter(index=1, title="Chapter 1", body="Body 1", char_count=6),
        Chapter(index=2, title="Chapter 2", body="Body 2", char_count=6),
    ]
    # Even with huge max_chars, each chapter must be in its own chunk
    chunks = chunk_by_chapter(chapters, max_chars=5000)
    assert len(chunks) == 2
    assert chunks[0].text == "Chapter 1\n\nBody 1"
    assert chunks[1].text == "Chapter 2\n\nBody 2"


def test_chunk_by_chapter_full_text_title_not_prepended():
    chapters = [
        Chapter(
            index=1,
            title="Full text",
            body="Single story content without chapter headings.",
            char_count=47,
        ),
    ]
    chunks = chunk_by_chapter(chapters, max_chars=500)
    assert len(chunks) == 1
    assert chunks[0].text == "Single story content without chapter headings."


def test_chunk_by_chapter_empty_and_whitespace():
    assert chunk_by_chapter([]) == []
    chapters_blank = [
        Chapter(index=1, title="", body="   \n\t  ", char_count=0),
    ]
    assert chunk_by_chapter(chapters_blank) == []
    chapters_full_text_empty = [
        Chapter(index=1, title="Full text", body="   \n\t  ", char_count=0),
    ]
    assert chunk_by_chapter(chapters_full_text_empty) == []

    # Non-empty heading without body (e.g. volume or standalone section) is emitted
    chapters_heading = [
        Chapter(index=1, title="Heading Only", body="   \n\t  ", char_count=0),
    ]
    chunks = chunk_by_chapter(chapters_heading)
    assert len(chunks) == 1
    assert chunks[0].text == "Heading Only"


def test_split_chapters_and_chunk_volume_heading_with_empty_body():
    # R1: Xianxia novels commonly have volume lines immediately preceding chapters:
    # 第一卷 少年出山
    # 第一章 天才少年
    # 正文……
    text = "第一卷 少年出山\n第一章 天才少年\n正文内容在这里。"
    chapters = split_chapters(text)
    assert len(chapters) == 2

    # Volume division has index=0 so Chapter 1 keeps index=1
    assert chapters[0].index == 0
    assert chapters[0].title == "第一卷 少年出山"
    assert chapters[0].body == ""

    assert chapters[1].index == 1
    assert chapters[1].title == "第一章 天才少年"
    assert chapters[1].body == "正文内容在这里。"

    # Chunks must preserve both volume heading and chapter title in narration
    chunks = chunk_by_chapter(chapters)
    assert len(chunks) == 2
    assert chunks[0].text == "第一卷 少年出山"
    assert "第一章 天才少年" in chunks[1].text
    assert "正文内容在这里。" in chunks[1].text


def test_split_chapters_regression_cases_from_review():
    # R2: Hard-wrapped prose without punctuation must NOT trigger heading split
    wrapped_vi = "Hồi 3 tuổi tôi ở quê với bà\nMột buổi trưa hè oi ả."
    chapters_vi = split_chapters(wrapped_vi)
    assert len(chapters_vi) == 1
    assert chapters_vi[0].title == "Full text"

    wrapped_en = "Chapter 3 was the best part of the\nbook according to the reviewer."
    chapters_en = split_chapters(wrapped_en)
    assert len(chapters_en) == 1
    assert chapters_en[0].title == "Full text"

    wrapped_zh = "第一回见到他时他很紧张\n在场的人都看出来了。"
    chapters_zh = split_chapters(wrapped_zh)
    assert len(chapters_zh) == 1
    assert chapters_zh[0].title == "Full text"

    # R2: Legitimate titles containing commas or quotes MUST be accepted
    text_en_comma = "Chapter 1: Hello, World\nContent starts here."
    ch_en_comma = split_chapters(text_en_comma)
    assert len(ch_en_comma) == 1
    assert ch_en_comma[0].title == "Chapter 1: Hello, World"
    assert ch_en_comma[0].body == "Content starts here."

    text_vi_comma = "Chương 5: Gió, mưa và lửa\nNội dung diễn biến."
    ch_vi_comma = split_chapters(text_vi_comma)
    assert len(ch_vi_comma) == 1
    assert ch_vi_comma[0].title == "Chương 5: Gió, mưa và lửa"
    assert ch_vi_comma[0].body == "Nội dung diễn biến."

    text_en_quote = 'Chapter 3: "The Return"\nContent begins.'
    ch_en_quote = split_chapters(text_en_quote)
    assert len(ch_en_quote) == 1
    assert ch_en_quote[0].title == 'Chapter 3: "The Return"'
    assert ch_en_quote[0].body == "Content begins."


@pytest.mark.parametrize(
    "heading",
    [
        "第一回\u3000启程",
        "第一卷\u3000少年出山",
        "第一节\u3000概述",
        "Chapter 1\u00a0The Beginning",
        "Chương 1\u00a0Mở đầu",
        "第一章\u3000天才少年",
        "CHAPTER I.",
        "Chương 12.",
    ],
)
def test_is_chapter_heading_unicode_whitespace_and_ordinal_dot(heading: str) -> None:
    # S1 & S3: Full-width ideographic space (\u3000), non-breaking space (\u00a0),
    # and ordinal period endings (CHAPTER I., Chương 12.) must be accepted.
    assert is_chapter_heading(heading) is True


def test_split_chapters_unicode_whitespace_volume_and_chapter() -> None:
    # S1 Acceptance: 第一卷\u3000X\n第一章\u3000Y\n正文 gives indices 0, 1
    text = "第一卷\u3000X\n第一章\u3000Y\n正文"
    chapters = split_chapters(text)
    assert len(chapters) == 2
    assert chapters[0].index == 0
    assert chapters[0].title == "第一卷\u3000X"
    assert chapters[0].body == ""
    assert chapters[1].index == 1
    assert chapters[1].title == "第一章\u3000Y"
    assert chapters[1].body == "正文"


@pytest.mark.parametrize(
    "line",
    [
        "Chapter 3. He walked in.",
        "Chapter 3: I came home late that night.",
        "Chương 3. Ông ấy bước vào nhà.",
        "Chapter 3:He left.",
        "Chapter 3 — he walked in",
        "Chapter 3- and then he was gone",
        "Chapter 3: I came home late that night",
        "Chương 3: Hắn bước vào phòng vắng",
        "Chương 3: Ông lão bước vào căn phòng tối",
        "Chương 3: Ta bước vào căn phòng tối",
        "Chương 3: Nàng mỉm cười nhìn hắn rồi đi",
        "Hồi 3 tuổi, cậu bé ...",
        "Book mild",
        "Act civil",
        "chapter iv",  # Lowercase Roman numerals intentionally not detected as trade-off for T2
        # Note (Y2 & Z2 trade-off): Multi-word sentence-case pronoun lines (e.g.
        # "Chương 9: Hắn là thiên tài sao", "Chương 3: Ông lão.") are intentionally
        # disqualified to prevent splitting narrative sentence lines.
    ],
)
def test_is_chapter_heading_disqualifies_sentences_and_english_words(line: str) -> None:
    # T1, T2, U2, V1, X2, Y3, Z1: Prose ending in period, lowercase phrases after delimiter,
    # sentence-cased pronoun-led sentences, and words matching Roman numerals must not be headings.
    assert is_chapter_heading(line) is False


@pytest.mark.parametrize(
    "line",
    [
        "Chương 7: Người Đưa Thư",
        "Chương 3: Ông Lão Bí Ẩn",
        "Chapter 2: It Begins",
        "Chương 5: Đêm...",
        "Chương 5: Đêm…",
        "Chương 10: Kết thúc.",
        "Chapter 1: The End.",
        "Chapter 1. The End.",
        "Chương 1. Kết thúc.",
        "Chapter 5: It Was a Dark Night",
        "Chương 9: Hắn Là Thiên Tài Sao",
        "Chương 9: Nó Đến Rồi Đây Này",
        "Chương 3: Ông Lão.",
        "Chapter 7: It Ends.",
        "Chapter 5: We Rise.",
        "Chương 3: Ta Trở Về.",
        "Chương 7: Nàng Là Ai",
        "Chương 12: Cô gái áo trắng trên lầu",
        "Chương 4: Lão già bí ẩn ở núi",
        "Chương 5: Y thuật thần kỳ của ta",
        "Chương 6: Anh hùng xuất thiếu niên",
        "Chương 9: Bà la sát hiện thân",
        "Chương 2: Cô gái.",
        "Chương 1: Thiếu niên áo trắng xuất hiện",
        "Tập 2: Đi.",
        "Chapter IV",
    ],
)
def test_is_chapter_heading_accepts_valid_short_titles_with_dots_or_ellipsis(line: str) -> None:
    # U1, V1, X1, X2, Y1, Z1, AA1: Valid titles with initial stopwords after delimiter,
    # dot delimiters, Title-Cased pronoun headings (even short/dotted), kinship noun titles,
    # periods, or ellipsis.
    assert is_chapter_heading(line) is True


def test_is_chapter_heading_unicode_whitespace_before_delimiter() -> None:
    # T3: Whitespace before delimiter should accept NBSP (\u00a0)
    assert is_chapter_heading("Chương 1\u00a0: Mở đầu") is True
    assert is_chapter_heading("Chapter 1\u00a0: The Beginning") is True
