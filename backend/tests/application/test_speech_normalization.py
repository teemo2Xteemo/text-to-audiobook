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
