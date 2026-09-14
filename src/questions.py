"""Flag to client question mapping.

CLAUDE.md Task 7. flags.py decides that a borrower deserves attention and says
why. This module turns that into what to ask the client, from
config/questions.yaml, so the wording can be changed without touching src/.

Two rules hold the file and the engine together, and tests enforce both:

    every flag in thresholds.yaml has an entry in questions.yaml, so no borrower
    page can show a flag with an empty question box

    every entry in questions.yaml names a flag that exists, so a renamed flag
    fails here rather than going quietly unasked

questions.yaml is deliberately not part of the Config dataclass. Nothing in it
drives a number, it is read only where a question is displayed or exported, and
keeping it out of load_config means the engine still loads on a checkout where
the file has not been written yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from src.config import (
    REPO_ROOT,
    Config,
    ConfigError,
    check_status,
    load_yaml_mapping,
    require,
)

QUESTIONS_FILENAME = "questions.yaml"
DEFAULT_QUESTIONS_PATH = REPO_ROOT / "config" / QUESTIONS_FILENAME

# One or two per flag, from CLAUDE.md Task 7. More than two is a questionnaire
# rather than a call, and a credit officer will read none of them.
MAX_QUESTIONS_PER_FLAG = 2


class QuestionsError(ConfigError):
    """questions.yaml is missing, malformed or out of step with the flags."""


@dataclass(frozen=True)
class Question:
    """One question to ask a client, and where it came from."""

    flag: str
    order: int
    text: str
    status: str
    source: str
    why: str


def questions_path(config_dir: Path | str | None = None) -> Path:
    """Where questions.yaml lives for a given config directory.

    A caller running against a fixture config directory that has no questions
    file falls back to the real config/questions.yaml. The questions carry no
    numbers, so there is no fixture version of them to keep separate, and the
    path that was actually read is recorded by every caller that reports
    provenance.
    """
    if config_dir is None:
        return DEFAULT_QUESTIONS_PATH
    candidate = Path(config_dir) / QUESTIONS_FILENAME
    return candidate if candidate.is_file() else DEFAULT_QUESTIONS_PATH


def _clean(text: Any) -> str:
    """Collapse the whitespace a folded yaml block leaves behind."""
    return " ".join(str(text).split())


def _check_entry(flag: str, entry: Mapping[str, Any]) -> tuple[str, str, str, list[str]]:
    where = f"{QUESTIONS_FILENAME} questions.{flag}"
    if not isinstance(entry, Mapping):
        raise QuestionsError(f"{where}: each flag entry must be a mapping")
    try:
        status = check_status(require(entry, "status", where), where)
        source = _clean(require(entry, "source", where))
        why = _clean(require(entry, "why", where))
        asked = require(entry, "ask", where)
    except ConfigError as exc:
        # Raised by the shared config helpers. Re-raised as this module's own
        # error so that a caller catching QuestionsError catches everything this
        # file can go wrong with.
        raise QuestionsError(str(exc)) from exc
    if not isinstance(asked, list) or not asked:
        raise QuestionsError(f"{where}: ask must be a non-empty list of questions")
    if len(asked) > MAX_QUESTIONS_PER_FLAG:
        raise QuestionsError(
            f"{where}: {len(asked)} questions, but a flag carries at most "
            f"{MAX_QUESTIONS_PER_FLAG}. A longer list is a questionnaire, not a call."
        )
    texts: list[str] = []
    for index, raw in enumerate(asked, start=1):
        text = _clean(raw)
        if not text:
            raise QuestionsError(f"{where}: question {index} is blank")
        if not text.endswith("?"):
            raise QuestionsError(
                f"{where}: question {index} does not end in a question mark, so it "
                f"is a statement rather than something to ask"
            )
        texts.append(text)
    return status, source, why, texts


def load_questions(
    path: Path | str | None = None, config_dir: Path | str | None = None
) -> dict[str, list[Question]]:
    """Read questions.yaml into flag code to questions, in file order.

    Pass path for one exact file, or config_dir to resolve it the way the rest
    of the tool does.
    """
    resolved = Path(path) if path is not None else questions_path(config_dir)
    try:
        data = load_yaml_mapping(resolved)
        if "meta" not in data:
            raise QuestionsError(f"{resolved.name}: missing the meta block")
        block = require(data, "questions", resolved.name)
    except QuestionsError:
        raise
    except ConfigError as exc:
        raise QuestionsError(str(exc)) from exc
    if not isinstance(block, Mapping) or not block:
        raise QuestionsError(f"{resolved.name}: questions must be a non-empty mapping")

    out: dict[str, list[Question]] = {}
    for flag, entry in block.items():
        status, source, why, texts = _check_entry(str(flag), entry)
        out[str(flag)] = [
            Question(
                flag=str(flag),
                order=index,
                text=text,
                status=status,
                source=source,
                why=why,
            )
            for index, text in enumerate(texts, start=1)
        ]
    return out


def flag_codes(config: Config) -> list[str]:
    """Every flag code thresholds.yaml defines, sorted."""
    flags = require(config.thresholds, "flags", "thresholds.yaml")
    return sorted(str(code) for code in flags)


def check_covers_flags(
    questions: Mapping[str, Sequence[Question]], config: Config
) -> None:
    """Fail loudly when the flags and the questions have drifted apart."""
    defined = set(flag_codes(config))
    asked = set(questions)
    missing = sorted(defined - asked)
    if missing:
        raise QuestionsError(
            f"{QUESTIONS_FILENAME} has no questions for {missing}. Every flag in "
            f"thresholds.yaml needs at least one, or a borrower page shows a flag "
            f"with an empty question box."
        )
    unknown = sorted(asked - defined)
    if unknown:
        raise QuestionsError(
            f"{QUESTIONS_FILENAME} asks about {unknown}, which thresholds.yaml does "
            f"not define. A renamed flag must be renamed in both files."
        )


def questions_for(
    flags: Iterable[str], questions: Mapping[str, Sequence[Question]]
) -> list[Question]:
    """The questions raised by one borrower's active flags.

    Flags are taken in the order given, which lets a caller put the flag that
    matters most first. A flag with no entry is an error rather than a silent
    gap, because silence is what this module exists to prevent.
    """
    out: list[Question] = []
    for flag in flags:
        if flag not in questions:
            raise QuestionsError(
                f"{QUESTIONS_FILENAME} has no questions for flag {flag!r}"
            )
        out.extend(questions[flag])
    return out


def as_rows(questions: Mapping[str, Sequence[Question]]) -> list[dict[str, str]]:
    """Flat rows for an export, sorted by flag then by the order in the file."""
    rows: list[dict[str, str]] = []
    for flag in sorted(questions):
        for question in questions[flag]:
            rows.append(
                {
                    "flag": flag,
                    "question_order": str(question.order),
                    "question_text": question.text,
                }
            )
    return rows
