import pytest

from quotient.grades import canonical_grade, default_grade


@pytest.mark.parametrize(
    ("text", "grade"),
    [
        ("technical", "technical"), ("Tech", "technical"), ("industrial grade", "technical"),
        ("fibre grade/technical", "technical"), ("HPLC", "reagent"), ("ACS reagent", "reagent"), ("AR", "reagent"),
        ("reagent grade, stabilised", "reagent"), ("USP", "pharma_usp"), ("USP-NF", "pharma_usp"), ("BP/USP", "pharma_usp"),
        ("Ph. Eur.", "pharma_ep"), ("EP", "pharma_ep"),
        ("USP/EP", None), ("food grade", None), ("membrane grade", None), (None, None), ("", None),
    ],
)
def test_grade_aliases(text, grade):
    assert canonical_grade(text) == grade


def test_an_unstated_grade_defaults_to_the_lowest_offered():
    assert default_grade(["pharma_ep", "technical", "pharma_usp"]) == "technical"
    assert default_grade(["pharma_ep", "pharma_usp"]) == "pharma_usp"
