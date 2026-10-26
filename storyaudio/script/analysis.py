"""Book analysis: the numbers the agent needs to propose how to tell a story.

    words, hours      how big the job is
    dialogue share    words inside quotes / all words
    speakers          named speakers ("said Peggy") with line counts
    person            first-person narration ("I", "my" outside quotes) or third
    front matter      very short chapters (TOC, copyright, dedication)

From these, `rank_forms` orders the story forms. The agent reads the text
as well and writes the proposal; the numbers are evidence, not the decision.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass, field, replace
from enum import Enum

from storyaudio.book import Book
from storyaudio.script.drafter import draft_script
from storyaudio.script.model import NARRATOR, Kind

WORDS_PER_HOUR = 9300        # ~155 words per minute, audiobook pace
FRONT_MATTER_WORDS = 150
LEAD_MIN_LINES = 8           # a named speaker with this many lines is a lead
MINOR_MIN_LINES = 2
FIRST_PERSON_MIN = 0.012     # share of narration words that are I/me/my/...
RICH_DIALOGUE = 0.25
SOME_DIALOGUE = 0.10

_QUOTED = re.compile(r"“[^”]*”|\"[^\"]*\"")
_WORD = re.compile(r"[A-Za-z’']+")
_FIRST_PERSON = frozenset({"i", "me", "my", "mine", "myself", "we", "us", "our"})


class Person(Enum):
    FIRST = "first"
    THIRD = "third"


class Fit(Enum):
    RECOMMENDED = "recommended"
    GOOD = "good"
    POSSIBLE = "possible"


@dataclass(frozen=True)
class ChapterStat:
    number: int
    title: str
    words: int
    dialogue_share: float


@dataclass(frozen=True)
class SpeakerStat:
    id: str
    name: str
    lines: int
    words: int


@dataclass(frozen=True)
class Form:
    id: str
    name: str
    mode: str        # faithful | adapted
    voices: int      # cast size including the narrator
    fit: Fit
    why: str


@dataclass(frozen=True)
class Analysis:
    title: str
    author: str
    words: int
    hours: float
    dialogue_share: float
    person: Person
    first_person_share: float
    chapters: list[ChapterStat]
    speakers: list[SpeakerStat]
    front_matter: list[int]
    forms: list[Form] = field(default_factory=list)

    def to_json(self) -> dict:
        data = asdict(self)
        data["person"] = self.person.value
        for form in data["forms"]:
            form["fit"] = form["fit"].value
        return data


def analyze_book(book: Book) -> Analysis:
    chapters = [_chapter_stat(i, c.title, c.paragraphs) for i, c in enumerate(book.chapters, start=1)]
    words = sum(c.words for c in chapters)
    quoted = sum(_quoted_words(p) for c in book.chapters for p in c.paragraphs)
    first = _first_person_share(book)

    analysis = Analysis(
        title=book.title,
        author=book.author,
        words=words,
        hours=round(words / WORDS_PER_HOUR, 2),
        dialogue_share=round(quoted / max(words, 1), 3),
        person=Person.FIRST if first >= FIRST_PERSON_MIN else Person.THIRD,
        first_person_share=round(first, 4),
        chapters=chapters,
        speakers=_speakers(book),
        front_matter=[c.number for c in chapters if c.words < FRONT_MATTER_WORDS],
    )
    return replace(analysis, forms=rank_forms(analysis))


def rank_forms(a: Analysis) -> list[Form]:
    """Every form, best fit first, each with the evidence behind its rank."""
    leads = [s for s in a.speakers if s.lines >= LEAD_MIN_LINES]
    minors = [s for s in a.speakers if MINOR_MIN_LINES <= s.lines < LEAD_MIN_LINES]
    dialogue = f"{a.dialogue_share:.0%} of words are dialogue"
    named = f"{len(leads)} named speakers with {LEAD_MIN_LINES}+ lines"

    full_fit = Fit.RECOMMENDED if a.dialogue_share >= RICH_DIALOGUE and len(leads) >= 2 else Fit.GOOD if leads else Fit.POSSIBLE
    leads_fit = Fit.RECOMMENDED if SOME_DIALOGUE <= a.dialogue_share < RICH_DIALOGUE and leads else Fit.GOOD if leads else Fit.POSSIBLE
    single_fit = Fit.RECOMMENDED if a.dialogue_share < SOME_DIALOGUE or not leads else Fit.GOOD

    forms = [
        Form("full-cast", "Full cast", "faithful", 1 + len(leads) + len(minors), full_fit,
             f"Every speaking character gets a voice; the narrator reads the rest. {dialogue}; {named}."),
        Form("narrator-leads", "Narrator + lead voices", "faithful", 1 + len(leads), leads_fit,
             f"Leads get their own voices; the narrator voices minor characters. Fewer clips to cast. {named}."),
        Form("single-narrator", "Single narrator", "faithful", 1, single_fit,
             f"One performer reads everything, classic audiobook style. {dialogue}."),
        Form("conversation", "Conversation retelling", "adapted", 2, Fit.POSSIBLE,
             "Two hosts retell and discuss the story. Not the author's words: the word check is off."),
    ]
    if a.person is Person.FIRST:
        forms.insert(0, Form("protagonist-narrates", "Protagonist narrates", "faithful", 1 + len(leads), Fit.RECOMMENDED,
                             f"First-person book ({a.first_person_share:.1%} of narration words are I/me/my): "
                             "the narrator voice is the main character; other speakers get voices."))

    order = list(Fit)
    return sorted(forms, key=lambda f: order.index(f.fit))


def _chapter_stat(number: int, title: str, paragraphs: list[str]) -> ChapterStat:
    words = sum(len(p.split()) for p in paragraphs)
    quoted = sum(_quoted_words(p) for p in paragraphs)
    return ChapterStat(number, title, words, round(quoted / max(words, 1), 3))


def _quoted_words(paragraph: str) -> int:
    return sum(len(m.group(0).split()) for m in _QUOTED.finditer(paragraph))


def _first_person_share(book: Book) -> float:
    """I/me/my in narration only: quoted speech is first person in any book."""
    counts = Counter()
    total = 0
    for chapter in book.chapters:
        for paragraph in chapter.paragraphs:
            words = [w.lower() for w in _WORD.findall(_QUOTED.sub(" ", paragraph))]
            total += len(words)
            counts.update(w for w in words if w in _FIRST_PERSON)
    return sum(counts.values()) / max(total, 1)


def _speakers(book: Book) -> list[SpeakerStat]:
    """Speakers the baseline draft could name, most lines first."""
    script = draft_script(book)
    lines: Counter[str] = Counter()
    words: Counter[str] = Counter()
    for chapter in script.chapters:
        for segment in chapter.segments:
            if segment.kind is not Kind.LINE or segment.speaker == NARRATOR:
                continue
            lines[segment.speaker] += 1
            words[segment.speaker] += len(segment.text.split())

    return [SpeakerStat(m.id, m.name, lines[m.id], words[m.id]) for m in sorted(
        (m for m in script.cast if m.id != NARRATOR), key=lambda m: -lines[m.id])]
