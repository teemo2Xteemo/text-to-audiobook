import pytest

from app.application.pipeline.speech_normalization import normalization_problem

ORIGINAL = (
    "She turned 20 on 3 May.\n"
    "The train left at 7:30 and cost $15, about 2 km from the old mill by the river."
)


def test_accepts_faithful_spoken_rewrite() -> None:
    spoken = (
        "She turned twenty on the third of May.\n"
        "The train left at seven thirty and cost fifteen dollars, about two kilometres "
        "from the old mill by the river."
    )
    assert normalization_problem(ORIGINAL, spoken) is None


@pytest.mark.parametrize(
    ("normalized", "reason"),
    [
        ("", "empty"),
        ("She turned twenty on the third of May.", "line structure"),
        (
            "Here is the rewritten text:\nShe turned twenty on the third of May.\n"
            "The train left at seven thirty.",
            "line structure",
        ),
        (
            "Here is the text: She turned twenty on the third of May.\n"
            "The train left at seven thirty and cost fifteen dollars, about two kilometres "
            "from the old mill by the river.",
            "opening words",
        ),
        ("She turned twenty.\nThe train left.", "letter ratio"),
        (
            "她在五月三日满二十岁。\n火车七点半出发，花了十五美元，离河边的旧磨坊大约两公里。",
            "new script",
        ),
        (ORIGINAL + " 2024", "more digits"),
    ],
)
def test_rejects_unfaithful_output(normalized: str, reason: str) -> None:
    problem = normalization_problem(ORIGINAL, normalized)
    assert problem is not None and reason in problem


@pytest.mark.parametrize(
    ("original", "spoken"),
    [
        (
            "1.250.000 linh thạch đã được chuyển vào kho của tông môn.",
            "Một triệu hai trăm năm mươi nghìn linh thạch đã được chuyển vào kho của tông môn.",
        ),
        (
            "1984 was the year everything changed.",
            "Nineteen eighty-four was the year everything changed.",
        ),
        ("TP.HCM là thành phố lớn nhất.", "Thành phố Hồ Chí Minh là thành phố lớn nhất."),
    ],
)
def test_accepts_rewrites_of_number_or_abbreviation_openings(original: str, spoken: str) -> None:
    assert normalization_problem(original, spoken) is None


def test_all_caps_heading_does_not_disable_preamble_check() -> None:
    original = "CHƯƠNG 12 HUYẾT CHIẾN THIÊN MA SƠN Lâm Phong rút kiếm."
    faithful = "CHƯƠNG mười hai HUYẾT CHIẾN THIÊN MA SƠN Lâm Phong rút kiếm."
    preamble = (
        "Đây là bản đã chuẩn hóa: chương mười hai huyết chiến thiên ma sơn Lâm Phong rút kiếm."
    )
    assert normalization_problem(original, faithful) is None
    problem = normalization_problem(original, preamble)
    assert problem is not None and "opening words" in problem
