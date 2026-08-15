"""The correction record and the category catalog."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel


class CorrectionCategory(StrEnum):
    """The labels the reviewer may use; anything else makes the batch fail to parse."""

    ARTICLES = "articles"
    PREPOSITIONS = "prepositions"
    VERB_TENSE = "verb-tense"
    VERB_FORM = "verb-form"
    WORD_FORM = "word-form"
    WORD_ORDER = "word-order"
    WORD_CHOICE = "word-choice"
    AGREEMENT = "agreement"
    PLURAL = "plural"
    SPELLING = "spelling"
    TYPO = "typo"
    PRONOUNS = "pronouns"
    BREVITY = "brevity"
    CALQUE = "calque"
    REGISTER = "register"
    OTHER = "other"


@dataclass(frozen=True)
class CategoryInfo:
    """One correction category: what the reviewer is told and what the user is shown."""

    kind: Literal["mistake", "style"]  # real English mistake vs correct-but-unnatural
    title: str
    description: str  # one lowercase sentence fragment; rendered into both the prompt line and the UI popover
    example: str | None  # tiny '"before" → "after"'; None only where no single example is representative


CATEGORIES: dict[CorrectionCategory, CategoryInfo] = {
    CorrectionCategory.ARTICLES: CategoryInfo(
        kind="mistake",
        title="Articles",
        description="a missing, extra, or wrong a/an/the",
        example='"I found bug in the parser" → "I found a bug in the parser"',
    ),
    CorrectionCategory.PREPOSITIONS: CategoryInfo(
        kind="mistake",
        title="Prepositions",
        description="a wrong or missing preposition",
        example='"it depends of the config" → "it depends on the config"',
    ),
    CorrectionCategory.VERB_TENSE: CategoryInfo(
        kind="mistake",
        title="Verb tense",
        description="a verb tense that does not match the time it describes",
        example='"I run it yesterday" → "I ran it yesterday"',
    ),
    CorrectionCategory.VERB_FORM: CategoryInfo(
        kind="mistake",
        title="Verb form",
        description="a wrong verb form after another verb — infinitive, -ing, or participle mixed up",
        example='"this allows to configure the port" → "this allows configuring the port"',
    ),
    CorrectionCategory.WORD_FORM: CategoryInfo(
        kind="mistake",
        title="Word form",
        description="the right word in the wrong part of speech or derivation",
        example='"it loads easy" → "it loads easily"',
    ),
    CorrectionCategory.WORD_ORDER: CategoryInfo(
        kind="mistake",
        title="Word order",
        description="words in an order a native would not use",
        example='"it returns always null" → "it always returns null"',
    ),
    CorrectionCategory.WORD_CHOICE: CategoryInfo(
        kind="mistake",
        title="Word choice",
        description="a word or set phrase that is not what a native would use here",
        example='"the actual version of the library" → "the current version of the library"',
    ),
    CorrectionCategory.AGREEMENT: CategoryInfo(
        kind="mistake",
        title="Agreement",
        description="a subject and verb, or pronoun and noun, that do not match in number",
        example='"the tests is failing" → "the tests are failing"',
    ),
    CorrectionCategory.PLURAL: CategoryInfo(
        kind="mistake",
        title="Plural",
        description="a wrong singular or plural form, often an uncountable noun",
        example='"add more informations" → "add more information"',  # codespell:ignore informations — intentional
    ),
    CorrectionCategory.SPELLING: CategoryInfo(
        kind="mistake",
        title="Spelling",
        description="a misspelling the writer would repeat — they believe the word is spelled that way",
        example='"habbits" → "habits"',  # codespell:ignore habbits — intentional
    ),
    CorrectionCategory.TYPO: CategoryInfo(
        kind="mistake",
        title="Typo",
        description="a one-off keyboard slip — a doubled word or a mangled word the writer clearly knows",
        example='"talk about about it" → "talk about it"',
    ),
    CorrectionCategory.PRONOUNS: CategoryInfo(
        kind="mistake",
        title="Pronouns",
        description="a wrong, missing, or extra pronoun",
        example='"the function fails when is called twice" → "the function fails when it is called twice"',
    ),
    CorrectionCategory.BREVITY: CategoryInfo(
        kind="style",
        title="Brevity",
        description="says in many words what a native developer would type in half or fewer; report only that large a saving",
        example='"in order to be able to run the tests" → "to run the tests"',
    ),
    CorrectionCategory.CALQUE: CategoryInfo(
        kind="style",
        title="Calque",
        description="a phrase translated word-for-word from the writer's native language",
        example='"I very like this approach" → "I really like this approach"',
    ),
    CorrectionCategory.REGISTER: CategoryInfo(
        kind="style",
        title="Register",
        description="wording too stiff or formal for developer chat",
        example='"Kindly proceed with the implementation" → "go ahead and implement it"',
    ),
    CorrectionCategory.OTHER: CategoryInfo(
        kind="mistake",
        title="Other",
        description="a real problem that fits no other label",
        example=None,
    ),
}

if set(CATEGORIES) != set(CorrectionCategory):
    raise RuntimeError("CATEGORIES must have exactly one entry per CorrectionCategory member")


class NewCorrection(BaseModel):
    """One correction as the reviewer reports it — a real mistake or a style fix — before it has a row."""

    message_id: int  # the message the fragment came from; sent with the batch and echoed back by the reviewer
    category: CorrectionCategory
    original: str  # wrong or unnatural fragment, verbatim from the message text
    corrected: str
    explanation: str


class Correction(BaseModel):
    """A stored correction; append-only, so its id is stable and a report can hold a range of them."""

    id: int
    message_id: int
    category: CorrectionCategory
    original: str  # wrong or unnatural fragment, verbatim from the message text
    corrected: str
    explanation: str
    extra_explanation: str | None  # the latest on-demand explanation the user asked for; never sent to reports
    typed_at: datetime  # when the message was typed; read off the joined message row, not a column of `corrections`
    acknowledged: bool  # the user has read and understood it

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Self:
        """Rebuild a correction from its row; the query must join the message's typed_at in."""
        return cls.model_validate(dict(row))
