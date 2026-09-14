"""Tests for config/questions.yaml and src/questions.py.

The value of this file is not the loader, which is small. It is the coupling
check: a flag added to thresholds.yaml with no question in questions.yaml would
reach a borrower page as an empty box, and nothing else in the suite would
notice. Two tests close that, one against the real config and one against the
fixture config the exporter runs on.

The rest are wording rules from CLAUDE.md, checked once here so they do not have
to be checked by eye on every borrower page.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.config import Config
from src.questions import (
    DEFAULT_QUESTIONS_PATH,
    MAX_QUESTIONS_PER_FLAG,
    Question,
    QuestionsError,
    as_rows,
    check_covers_flags,
    flag_codes,
    load_questions,
    questions_for,
    questions_path,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures"

# Words the question text must never contain. Tier numbers and flag codes are
# the tool's internals, and CLAUDE.md section 9 keeps them off any screen where
# a plain-language label exists. A question is read out to a client, which is the
# strongest form of that rule.
FORBIDDEN_IN_QUESTIONS = ("tier", "flag", "cbam declarant status is", "nace")


@pytest.fixture(scope="module")
def questions() -> dict[str, list[Question]]:
    return load_questions()


# ---------------------------------------------------------------------------
# The file itself
# ---------------------------------------------------------------------------


def test_the_file_ships_and_loads() -> None:
    assert DEFAULT_QUESTIONS_PATH.is_file()
    loaded = load_questions()
    assert loaded, "questions.yaml loaded but is empty"


def test_a_fixture_config_directory_falls_back_to_the_real_questions_file() -> None:
    """The questions carry no numbers, so there is no fixture version of them."""
    assert not (FIXTURE_DIR / "questions.yaml").exists()
    assert questions_path(FIXTURE_DIR) == DEFAULT_QUESTIONS_PATH
    assert questions_path(None) == DEFAULT_QUESTIONS_PATH


def test_every_flag_carries_one_or_two_questions(questions) -> None:
    for flag, asked in questions.items():
        assert 1 <= len(asked) <= MAX_QUESTIONS_PER_FLAG, flag
        assert [q.order for q in asked] == list(range(1, len(asked) + 1))


def test_every_question_is_a_question_in_plain_english(questions) -> None:
    for flag, asked in questions.items():
        for question in asked:
            assert question.text.endswith("?"), f"{flag}: {question.text}"
            assert len(question.text.split()) >= 6, f"{flag}: too terse to be useful"
            # CLAUDE.md section 2: plain English, no em dashes.
            assert "—" not in question.text, f"{flag}: em dash in a question"
            lowered = question.text.lower()
            for banned in FORBIDDEN_IN_QUESTIONS:
                assert banned not in lowered, f"{flag}: question names {banned!r}"
            assert question.flag == flag


def test_no_question_restates_a_number_that_lives_in_another_config_file(
    questions,
) -> None:
    """The mass threshold, the pass-through share and the band gap all live in
    other config files and can change there. A question that restated one would
    drift out of step with the file that owns it.
    """
    for flag, asked in questions.items():
        for question in asked:
            assert not any(character.isdigit() for character in question.text), (
                f"{flag}: a question carries a number, which belongs in the config "
                f"file that owns it: {question.text}"
            )


def test_every_entry_is_marked_an_assumption_written_by_the_owner(questions) -> None:
    for flag, asked in questions.items():
        for question in asked:
            assert question.status == "assumption", flag
            assert question.source == "owner", flag
            assert question.why, f"{flag}: no line saying what the answer is for"


# ---------------------------------------------------------------------------
# The coupling to thresholds.yaml, which is the point of this file
# ---------------------------------------------------------------------------


def test_every_flag_in_the_real_config_has_questions(
    questions, real_config: Config
) -> None:
    check_covers_flags(questions, real_config)
    assert set(questions) == set(flag_codes(real_config))


def test_every_flag_in_the_fixture_config_has_questions(
    questions, fixture_config: Config
) -> None:
    """The exporter runs on the fixture config, so it needs the same coverage."""
    check_covers_flags(questions, fixture_config)


def test_a_missing_flag_is_reported_rather_than_left_silent(
    questions, real_config: Config
) -> None:
    thinned = {key: value for key, value in questions.items() if key != "RATING_GAP"}
    with pytest.raises(QuestionsError, match="RATING_GAP"):
        check_covers_flags(thinned, real_config)


def test_a_question_about_a_flag_that_does_not_exist_is_reported(
    questions, real_config: Config
) -> None:
    extra = dict(questions)
    extra["INVENTED_FLAG"] = []
    with pytest.raises(QuestionsError, match="INVENTED_FLAG"):
        check_covers_flags(extra, real_config)


# ---------------------------------------------------------------------------
# Mapping a borrower's active flags to its questions
# ---------------------------------------------------------------------------


def test_questions_for_follows_the_order_the_flags_are_given_in(questions) -> None:
    asked = questions_for(["RATING_GAP", "NO_DECLARANT"], questions)
    assert [q.flag for q in asked][0] == "RATING_GAP"
    assert "NO_DECLARANT" in {q.flag for q in asked}
    assert len(asked) == len(questions["RATING_GAP"]) + len(questions["NO_DECLARANT"])


def test_a_borrower_with_no_flags_is_asked_nothing(questions) -> None:
    assert questions_for([], questions) == []


def test_an_unknown_flag_raises_rather_than_returning_nothing(questions) -> None:
    with pytest.raises(QuestionsError, match="NOT_A_FLAG"):
        questions_for(["NOT_A_FLAG"], questions)


# ---------------------------------------------------------------------------
# Export shape
# ---------------------------------------------------------------------------


def test_rows_are_flat_sorted_and_carry_the_three_export_columns(questions) -> None:
    rows = as_rows(questions)
    assert rows
    assert list(rows[0]) == ["flag", "question_order", "question_text"]
    assert [row["flag"] for row in rows] == sorted(row["flag"] for row in rows)
    assert len(rows) == sum(len(asked) for asked in questions.values())


# ---------------------------------------------------------------------------
# Malformed files fail on load rather than later
# ---------------------------------------------------------------------------


def write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "questions.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_a_file_with_no_meta_block_is_rejected(tmp_path: Path) -> None:
    path = write(tmp_path, "questions:\n  MONITOR:\n    status: assumption\n")
    with pytest.raises(QuestionsError, match="meta"):
        load_questions(path)


def test_a_statement_instead_of_a_question_is_rejected(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        "meta:\n  config_version: '1.0.0'\n"
        "questions:\n  MONITOR:\n    status: assumption\n    source: owner\n"
        "    why: because\n    ask:\n      - 'Ask them about the volume.'\n",
    )
    with pytest.raises(QuestionsError, match="question mark"):
        load_questions(path)


def test_more_than_two_questions_is_rejected(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        "meta:\n  config_version: '1.0.0'\n"
        "questions:\n  MONITOR:\n    status: assumption\n    source: owner\n"
        "    why: because\n    ask:\n      - 'One?'\n      - 'Two?'\n      - 'Three?'\n",
    )
    with pytest.raises(QuestionsError, match="at most"):
        load_questions(path)


def test_an_unknown_status_is_rejected(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        "meta:\n  config_version: '1.0.0'\n"
        "questions:\n  MONITOR:\n    status: probably\n    source: owner\n"
        "    why: because\n    ask:\n      - 'What is the volume?'\n",
    )
    with pytest.raises(QuestionsError, match="status"):
        load_questions(path)
