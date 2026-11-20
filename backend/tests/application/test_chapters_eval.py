"""Precision and recall of chapter detection on hand-labelled excerpts.

Run ``make chapters-eval`` to print the per-language report. The same
assertions run in the default pytest suite, so a regression fails CI.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from app.application.pipeline.chapters import ChapterKind, split_chapters

_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "chapters"
_REQUIRED_FEATURES = {
    "hard-wrap",
    "nbsp",
    "ideographic-space",
    "volume",
    "preface",
    "all-caps",
    "mixed",
}


@dataclass(frozen=True)
class _Score:
    true_positive: int
    false_positive: int
    false_negative: int

    @property
    def precision(self) -> float:
        total = self.true_positive + self.false_positive
        return 1.0 if total == 0 else self.true_positive / total

    @property
    def recall(self) -> float:
        total = self.true_positive + self.false_negative
        return 1.0 if total == 0 else self.true_positive / total


def _load_books() -> list[dict]:
    payload = json.loads((_ROOT / "books.json").read_text(encoding="utf-8"))
    return payload["books"]


def _score_book(book: dict) -> tuple[_Score, list[str]]:
    text = (_ROOT / book["path"]).read_text(encoding="utf-8")
    chapters = split_chapters(text)
    ordinals = [chapter.ordinal for chapter in chapters]
    problems: list[str] = []
    if len(ordinals) != len(set(ordinals)):
        problems.append(f"{book['id']}: ordinals are not unique")
    if any(chapter.index != chapter.ordinal for chapter in chapters):
        problems.append(f"{book['id']}: index and ordinal differ")
    detected = Counter(
        (chapter.title, chapter.kind.value)
        for chapter in chapters
        if chapter.kind is not ChapterKind.PREFACE
    )
    labeled = Counter((item["title"], item["kind"]) for item in book["headings"])
    true_positive = sum((detected & labeled).values())
    false_positive = sum(detected.values()) - true_positive
    false_negative = sum(labeled.values()) - true_positive
    if detected != labeled:
        extra = list((detected - labeled).elements())
        missing = list((labeled - detected).elements())
        problems.append(f"{book['id']}: extra={extra} missing={missing}")
    return _Score(true_positive, false_positive, false_negative), problems


def _report(books: list[dict]) -> str:
    lines = ["chapter detection on labelled excerpts"]
    by_lang: dict[str, list[_Score]] = {}
    for book in books:
        score, problems = _score_book(book)
        by_lang.setdefault(book["lang"], []).append(score)
        lines.append(
            f"  {book['id']:<16} precision={score.precision:.3f} "
            f"recall={score.recall:.3f} "
            f"tp={score.true_positive} fp={score.false_positive} fn={score.false_negative}"
        )
        lines.extend(f"    {problem}" for problem in problems)
    lines.append("per language")
    for lang in ("zh", "vi", "en"):
        parts = by_lang.get(lang, [])
        total = _Score(
            sum(part.true_positive for part in parts),
            sum(part.false_positive for part in parts),
            sum(part.false_negative for part in parts),
        )
        lines.append(
            f"  {lang}: precision={total.precision:.3f} recall={total.recall:.3f} "
            f"tp={total.true_positive} fp={total.false_positive} fn={total.false_negative}"
        )
    return "\n".join(lines)


def test_book_fixtures_meet_precision_and_recall() -> None:
    books = _load_books()
    counts = Counter(book["lang"] for book in books)
    assert counts["zh"] >= 3
    assert counts["vi"] >= 3
    assert counts["en"] >= 2
    features = {feature for book in books for feature in book["features"]}
    assert _REQUIRED_FEATURES <= features

    report = _report(books)
    print(report)

    for book in books:
        score, problems = _score_book(book)
        assert not problems
        assert score.false_positive == 0
        assert score.false_negative == 0

    by_lang: dict[str, _Score] = {}
    for book in books:
        score, _problems = _score_book(book)
        current = by_lang.get(book["lang"], _Score(0, 0, 0))
        by_lang[book["lang"]] = _Score(
            current.true_positive + score.true_positive,
            current.false_positive + score.false_positive,
            current.false_negative + score.false_negative,
        )
    for lang, score in by_lang.items():
        assert score.recall >= 0.99, report
        assert score.precision >= 0.98, report
