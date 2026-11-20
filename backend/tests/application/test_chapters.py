import json
import time
from pathlib import Path

import pytest

from app.application.pipeline.chapters import (
    Chapter,
    ChapterKind,
    chunk_by_chapter,
    is_chapter_heading,
    split_chapters,
    split_chapters_detailed,
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
    # S2: index/ordinal are unique across preface and chapters. The printed
    # chapter number lives on chapter_number, so chapter 1 is not index 1
    # once a preface occupies ordinal 1.
    assert chapters[0].kind is ChapterKind.PREFACE
    assert chapters[0].index == 1
    assert chapters[0].ordinal == 1
    assert chapters[0].chapter_number is None
    assert chapters[0].title == ""
    assert "序言\n这是一段前言。" in chapters[0].body

    assert chapters[1].kind is ChapterKind.CHAPTER
    assert chapters[1].index == 2
    assert chapters[1].ordinal == 2
    assert chapters[1].chapter_number == 1
    assert chapters[1].title == "第一章 启程"
    assert chapters[1].body == "正文开始。"

    assert chapters[2].index == 3
    assert chapters[2].ordinal == 3
    assert chapters[2].chapter_number == 2
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
    assert chapters[0].ordinal == 1
    assert chapters[0].kind is ChapterKind.CHAPTER
    assert chapters[0].chapter_number is None
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

    # Volume is its own span. It no longer shares index 0 with a preface.
    assert chapters[0].kind is ChapterKind.VOLUME
    assert chapters[0].index == 1
    assert chapters[0].ordinal == 1
    assert chapters[0].chapter_number is None
    assert chapters[0].title == "第一卷 少年出山"
    assert chapters[0].body == ""

    assert chapters[1].kind is ChapterKind.CHAPTER
    assert chapters[1].index == 2
    assert chapters[1].ordinal == 2
    assert chapters[1].chapter_number == 1
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
    # S1: ideographic space stays in the title. S2: volume and chapter indices differ.
    text = "第一卷\u3000X\n第一章\u3000Y\n正文"
    chapters = split_chapters(text)
    assert len(chapters) == 2
    assert chapters[0].kind is ChapterKind.VOLUME
    assert chapters[0].index == 1
    assert chapters[0].ordinal == 1
    assert chapters[0].title == "第一卷\u3000X"
    assert chapters[0].body == ""
    assert chapters[1].kind is ChapterKind.CHAPTER
    assert chapters[1].index == 2
    assert chapters[1].ordinal == 2
    assert chapters[1].chapter_number == 1
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
        "Chương 2: Anh ấy bước vào phòng",
        "Chương 1: Anh ta ngẩng đầu lên nhìn trời",
        "Chương 2: Cô ấy mỉm cười rồi quay đi",
        "Chương 3: Ta bước vào căn phòng tối",
        "Chương 3: Nàng mỉm cười nhìn hắn rồi đi",
        "Hồi 3 tuổi, cậu bé ...",
        "Book mild",
        "Act civil",
        "chapter iv",  # T2: lowercase Roman numerals are intentionally not headings.
        # Y2 stays a line-level reject: a sentence-case unambiguous pronoun
        # ("Chương 9: Hắn là thiên tài sao") is prose, so "Hắn bước vào phòng vắng"
        # is not read as a title. Short "Ông lão." (Z2) is a title and is accepted.
    ],
)
def test_is_chapter_heading_disqualifies_sentences_and_english_words(line: str) -> None:
    # T1, T2, U2, V1, X2, Y3, Z1, BB1: Prose ending in period, lowercase phrases after delimiter,
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
        "Chương 4: Lão tổ lại xuất thế",
        "Chương 3: Anh hùng đã trở lại nơi cũ",
        "Chương 8: Cô gái đi lạc giữa rừng sâu",
        "Chương 15: Ông lão đang chờ ở cổng",
        "Tập 2: Đi.",
        "Chapter IV",
    ],
)
def test_is_chapter_heading_accepts_valid_short_titles_with_dots_or_ellipsis(line: str) -> None:
    # U1, V1, X1, X2, Y1, Z1, AA1, BB1: Valid titles with initial stopwords after delimiter,
    # dot delimiters, Title-Cased pronoun headings (even short/dotted), kinship noun titles,
    # periods, or ellipsis.
    assert is_chapter_heading(line) is True


def test_is_chapter_heading_unicode_whitespace_before_delimiter() -> None:
    # T3: Whitespace before delimiter should accept NBSP (\u00a0)
    assert is_chapter_heading("Chương 1\u00a0: Mở đầu") is True
    assert is_chapter_heading("Chapter 1\u00a0: The Beginning") is True


_CORPUS_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "chapters" / "headings.json"
_CORPUS_ROWS = json.loads(_CORPUS_PATH.read_text(encoding="utf-8"))["rows"]


def _assert_corpus_row(row: dict) -> None:
    line = row["line"]
    expect_detect = row["expect"] == "detect"
    if row.get("scope", "line") == "line":
        assert is_chapter_heading(line) is expect_detect
        sample = f"{line}\nNội dung theo sau để còn một thân chương."
        titles = [chapter.title for chapter in split_chapters(sample)]
        assert (line in titles) is expect_detect
        return

    chapters = split_chapters(row["document"])
    titles = [chapter.title for chapter in chapters]
    assert (line in titles) is expect_detect
    for keep in row.get("keep_titles", []):
        assert keep in titles
    if row.get("kind"):
        match = next(chapter for chapter in chapters if chapter.title == line)
        assert match.kind.value == row["kind"]


@pytest.mark.parametrize(
    "row",
    [row for row in _CORPUS_ROWS if row["group"] == "must"],
    ids=lambda row: row["id"],
)
def test_heading_corpus_must(row: dict) -> None:
    _assert_corpus_row(row)


@pytest.mark.parametrize(
    "row",
    [row for row in _CORPUS_ROWS if row["group"] == "tradeoff"],
    ids=lambda row: row["id"],
)
def test_heading_corpus_tradeoff(row: dict) -> None:
    try:
        _assert_corpus_row(row)
    except AssertionError as exc:
        if row.get("xfail"):
            pytest.xfail(f"{row['source']}: {row.get('notes', 'known trade-off')} ({exc})")
        raise


def test_corpus_covers_tradeoff_finding_ids() -> None:
    tradeoff_sources = {row["source"] for row in _CORPUS_ROWS if row["group"] == "tradeoff"}
    assert tradeoff_sources >= {"CC2", "CC3", "Z2", "Y2", "T2", "T4"}


def test_volume_restart_and_preface_have_unique_ordinals() -> None:
    text = "序言\n前言。\n第一卷 上\n第一章 一\n正文一。\n第二卷 下\n第一章 二\n正文二。"
    chapters = split_chapters(text)
    assert [chapter.kind for chapter in chapters] == [
        ChapterKind.PREFACE,
        ChapterKind.VOLUME,
        ChapterKind.CHAPTER,
        ChapterKind.VOLUME,
        ChapterKind.CHAPTER,
    ]
    ordinals = [chapter.ordinal for chapter in chapters]
    assert ordinals == [1, 2, 3, 4, 5]
    assert [chapter.index for chapter in chapters] == ordinals
    assert len(set(ordinals)) == len(ordinals)
    assert [chapter.chapter_number for chapter in chapters] == [None, None, 1, None, 1]
    detail = split_chapters_detailed(text)
    assert detail.uncertain is False


def test_stray_heading_inside_later_chapter_is_not_split() -> None:
    text = (
        "Chương 55: Gió nổi\n"
        "Nội dung năm mươi lăm đủ dài để không bị coi là tiêu đề ngắn.\n\n"
        "Chương 56: Mưa đêm\n"
        "Nội dung năm mươi sáu tiếp tục câu chuyện trong đêm mưa.\n\n"
        "Chương 3: Ông lão bước vào căn phòng tối\n"
        "Nội dung lạc giữa chương.\n\n"
        "Chương 57: Lửa\n"
        "Nội dung năm mươi bảy khép lại đoạn này.\n"
    )
    titles = [chapter.title for chapter in split_chapters(text)]
    assert "Chương 3: Ông lão bước vào căn phòng tối" not in titles
    assert titles == ["Chương 55: Gió nổi", "Chương 56: Mưa đêm", "Chương 57: Lửa"]


def test_vietnamese_all_caps_headings_are_chapters() -> None:
    text = "CHƯƠNG 1: MỞ ĐẦU\nNội dung.\n\nCHƯƠNG 2: CHÚNG TA LÊN ĐƯỜNG\nTiếp tục.\n"
    chapters = split_chapters(text)
    assert [chapter.title for chapter in chapters] == [
        "CHƯƠNG 1: MỞ ĐẦU",
        "CHƯƠNG 2: CHÚNG TA LÊN ĐƯỜNG",
    ]
    assert [chapter.chapter_number for chapter in chapters] == [1, 2]


def _numbered_story(numbers: list[int], *, prefix: str = "Chương", title: str = "Đoạn") -> str:
    parts: list[str] = []
    for number in numbers:
        parts.append(f"{prefix} {number}: {title} {number}")
        parts.append(f"Nội dung của mục {number}.")
        parts.append("")
    return "\n".join(parts)


def test_forward_numbering_typo_does_not_swallow_later_chapters() -> None:
    # 30 is a typo where 3 was meant. Chapters 4..10 must still split.
    numbers = [1, 2, 30, *range(4, 11)]
    chapters = [
        chapter
        for chapter in split_chapters(_numbered_story(numbers))
        if chapter.kind is ChapterKind.CHAPTER
    ]
    kept = [chapter.chapter_number for chapter in chapters]
    assert kept == [1, 2, *range(4, 11)]
    assert 30 not in kept


def test_real_forward_gap_is_kept_when_the_new_run_continues() -> None:
    detail = split_chapters_detailed(_numbered_story([1, 2, 20, 21, 22]))
    assert [chapter.chapter_number for chapter in detail.chapters] == [1, 2, 20, 21, 22]
    assert detail.uncertain is False


def test_small_forward_typo_does_not_swallow_the_chapters_inside_the_jump() -> None:
    # 12 written as 21. Chapters 13..20 stay. The later real 21 is kept once.
    numbers = [*range(1, 12), 21, *range(13, 31)]
    detail = split_chapters_detailed(_numbered_story(numbers))
    kept = [chapter.chapter_number for chapter in detail.chapters]
    assert kept == [*range(1, 12), *range(13, 31)]
    assert kept.count(21) == 1
    assert detail.uncertain is True


def test_forward_typo_of_ten_keeps_the_chapters_it_skipped() -> None:
    numbers = [*range(1, 6), 16, *range(7, 26)]
    detail = split_chapters_detailed(_numbered_story(numbers))
    kept = [chapter.chapter_number for chapter in detail.chapters]
    assert kept == [*range(1, 6), *range(7, 26)]
    assert kept.count(16) == 1
    assert detail.uncertain is True


def test_forward_typo_near_the_start_keeps_the_following_run() -> None:
    numbers = [1, 16, *range(3, 20)]
    detail = split_chapters_detailed(_numbered_story(numbers))
    kept = [chapter.chapter_number for chapter in detail.chapters]
    assert kept == [1, *range(3, 20)]
    assert kept.count(16) == 1
    assert detail.uncertain is True


def test_real_small_gap_is_kept() -> None:
    numbers = [*range(1, 11), *range(12, 21)]
    detail = split_chapters_detailed(_numbered_story(numbers))
    kept = [chapter.chapter_number for chapter in detail.chapters]
    assert kept == [*range(1, 11), *range(12, 21)]
    assert detail.uncertain is False


def test_backward_stray_inside_a_late_chapter_is_dropped() -> None:
    numbers = [*range(1, 58), 3, *range(58, 61)]
    chapters = split_chapters(_numbered_story(numbers))
    kept = [chapter.chapter_number for chapter in chapters]
    assert kept == [*range(1, 58), *range(58, 61)]
    assert kept.count(3) == 1


def test_backward_typo_does_not_hide_the_following_chapters() -> None:
    text = _numbered_story([1, 2, 3, 2, 4, 5])
    chapters = split_chapters(text)
    kept = [chapter.chapter_number for chapter in chapters]
    assert kept[:3] == [1, 2, 3]
    assert 4 in kept and 5 in kept
    assert kept[-2:] == [4, 5]


def test_duplicate_chapter_numbers_are_both_kept() -> None:
    chapters = split_chapters(_numbered_story([1, 2, 2, 3]))
    assert [chapter.chapter_number for chapter in chapters] == [1, 2, 2, 3]


def test_second_book_without_a_volume_line_restarts_and_is_uncertain() -> None:
    text = _numbered_story([1, 2, 3, 4, 5, 1, 2, 3, 4, 5])
    detail = split_chapters_detailed(text)
    assert [chapter.chapter_number for chapter in detail.chapters] == [1, 2, 3, 4, 5, 1, 2, 3, 4, 5]
    assert detail.uncertain is True


def test_positional_chinese_numerals_without_unit_marks() -> None:
    samples = [
        ("第一零五章\n正文", 105),
        ("第二〇二四章\n正文", 2024),
        ("第一百零五章\n正文", 105),
    ]
    for text, number in samples:
        assert split_chapters(text)[0].chapter_number == number


def test_split_five_thousand_chapters_stays_fast() -> None:
    lines: list[str] = []
    for number in range(1, 5001):
        lines.append(f"第{number}章 标题")
        lines.append("正文一行。")
    started = time.perf_counter()
    chapters = split_chapters("\n".join(lines))
    elapsed = time.perf_counter() - started
    assert elapsed < 3.0
    assert sum(1 for chapter in chapters if chapter.kind is ChapterKind.CHAPTER) == 5000


def test_printed_numbers_cover_han_fullwidth_and_roman() -> None:
    samples = [
        ("第十二章\n正文", 12),
        ("第１章 开端\n正文", 1),
        ("第一百零八章 大结局\n正文", 108),
        ("CHAPTER XII.\nText", 12),
        ("Book II\nText", 2),
    ]
    for text, number in samples:
        chapter = split_chapters(text)[0]
        assert chapter.chapter_number == number
        assert chapter.kind is ChapterKind.CHAPTER
