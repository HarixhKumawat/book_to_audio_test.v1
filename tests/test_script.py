"""Markup, Markdown format, drafter, validator and job planner."""

from pathlib import Path

import pytest

from storyaudio.book import Book, Chapter
from storyaudio.config import TagPolicy
from storyaudio.ingest.text import SCENE_BREAK
from storyaudio.script import fmt, markup
from storyaudio.script.drafter import draft_script
from storyaudio.script.model import NARRATOR, Kind, Mode
from storyaudio.script.plan import Boundary, plan_jobs
from storyaudio.script.validator import Severity, check_script, has_errors

CAST = """# Cast

Title: Tiny
Author: Me
Mode: faithful

| ID | Name | Voice |
| --- | --- | --- |
| NARRATOR | Narrator | warm narrator |
| PEGGY | Peggy | bright young woman |
"""


def _book(*paragraphs: str, title: str = "One") -> Book:
    return Book("Tiny", "Me", "en", "tiny.txt", [Chapter(title, list(paragraphs))])


def _script(chapter_md: str):
    script = fmt.parse_cast(CAST)
    script.chapters.append(fmt.parse_chapter(chapter_md, 1, "01-one.md"))
    return script


# ---------------------------------------------------------------- markup


def test_spoken_strips_all_tags():
    assert markup.spoken("[icy calm] I <emphasis>never</emphasis> said [pause] that.") == "I never said that."


def test_words_normalize_quotes_and_case():
    assert markup.words("[sighs] “Don’t—STOP!”") == ["don't", "stop"]


def test_leading_mood_ignores_events():
    assert markup.leading_mood("[icy calm] Go.") == "icy calm"
    assert markup.leading_mood("[sighs] Go.") is None


def test_split_keeps_closing_quote_with_sentence():
    pieces = markup.split_text('"You\'re bleeding!" she cried. He said nothing at all, again.', 30)

    assert pieces == ['"You\'re bleeding!" she cried.', "He said nothing at all, again."]


def test_split_never_breaks_a_span():
    text = "Start here. <slow>Very. Slowly. Now.</slow> End."
    pieces = markup.split_text(text, 15)

    assert any("<slow>Very. Slowly. Now.</slow>" in p for p in pieces)
    assert " ".join(pieces).split() == text.split()


def test_split_respects_limit_for_long_prose():
    text = " ".join(f"Sentence number {n} is here." for n in range(50))
    pieces = markup.split_text(text, 120)

    assert all(len(p) <= 120 for p in pieces)
    assert " ".join(pieces) == text


@pytest.mark.parametrize("policy, expected", [
    (TagPolicy.KEEP, "[icy calm] Go <whisper>now</whisper>. [sighs]"),
    (TagPolicy.EVENTS_ONLY, "Go now. [sighs]"),
    (TagPolicy.NONE, "Go now."),
])
def test_tag_policy(policy, expected):
    assert markup.for_tts("[icy calm] Go <whisper>now</whisper>. [sighs]", policy) == expected


# ---------------------------------------------------------------- format


def test_parse_chapter_segments_and_continuations():
    chapter = fmt.parse_chapter(
        "# One\n\nNARRATOR: It rained.\nStill raining.\n\n<!-- note -->\nPEGGY: [bright] \"Hi.\"\n\n---\n\nNARRATOR: Later.\n",
        1,
    )

    kinds = [s.kind for s in chapter.segments]
    assert kinds == [Kind.TITLE, Kind.LINE, Kind.LINE, Kind.SCENE, Kind.LINE]
    assert chapter.segments[1].text == "It rained. Still raining."
    assert chapter.segments[2].speaker == "PEGGY"
    assert chapter.title == "One"


def test_parse_chapter_rejects_unlabelled_line():
    with pytest.raises(fmt.ScriptFormatError, match=":3:"):
        fmt.parse_chapter("# One\n\nno label here\n", 1, "f.md")


def test_cast_requires_narrator_and_valid_mode():
    with pytest.raises(fmt.ScriptFormatError, match="NARRATOR"):
        fmt.parse_cast("| ID | Name | Voice |\n| --- | --- | --- |\n| PEGGY | Peggy | x |\n")
    with pytest.raises(fmt.ScriptFormatError, match="Mode"):
        fmt.parse_cast(CAST.replace("faithful", "loose"))


def test_write_and_read_round_trip(tmp_path: Path):
    script = draft_script(_book("It rained.", '"Hi," said Peggy.', SCENE_BREAK, "End."))
    fmt.write_script(script, tmp_path)

    again = fmt.read_script(tmp_path)

    assert [m.id for m in again.cast] == [m.id for m in script.cast]
    assert [(s.kind, s.speaker, s.text) for s in again.chapters[0].segments] == [
        (s.kind, s.speaker, s.text) for s in script.chapters[0].segments
    ]


# ---------------------------------------------------------------- drafter


def test_draft_attributes_named_speaker_and_keeps_words():
    book = _book('"Morning, Peggy," said Oliver with a smile.', '"Morning," she answered.')
    script = draft_script(book)
    segments = script.chapters[0].segments

    assert [m.id for m in script.cast] == [NARRATOR, "OLIVER"]
    assert (segments[1].speaker, segments[1].text) == ("OLIVER", '"Morning, Peggy,"')
    assert (segments[2].speaker, segments[2].text) == (NARRATOR, "said Oliver with a smile.")
    assert segments[3].speaker == NARRATOR  # unattributed speech stays with the narrator
    assert not has_errors(check_script(script, book))


def test_draft_ignores_pronouns_and_names_inside_quotes():
    script = draft_script(_book('"Ask Mary," he said.'))

    assert [m.id for m in script.cast] == [NARRATOR]


def test_draft_scene_break_becomes_scene_segment():
    script = draft_script(_book("A.", SCENE_BREAK, "B."))

    assert [s.kind for s in script.chapters[0].segments] == [Kind.TITLE, Kind.LINE, Kind.SCENE, Kind.LINE]


# ---------------------------------------------------------------- validator


def _messages(script, book):
    return [(i.severity, i.message) for i in check_script(script, book)]


def test_validator_accepts_tagged_faithful_script():
    book = _book("It rained.", '"Hi," she said.')
    script = _script('# One\n\nNARRATOR: [gloomy] It rained.\n\nPEGGY: [bright] "Hi,"\n\nNARRATOR: she said. [pause]\n')

    assert not has_errors(check_script(script, book))


def test_validator_catches_changed_words():
    book = _book("It rained all day.")
    script = _script("# One\n\nNARRATOR: It poured all day.\n\nPEGGY: x\n")

    errors = [m for s, m in _messages(script, book) if s is Severity.ERROR]
    assert any("'rained' -> script 'poured'" in m for m in errors)


def test_validator_adapted_mode_skips_word_check():
    book = _book("It rained all day.")
    script = _script("# One\n\nNARRATOR: It poured all day.\n\nPEGGY: Hi.\n")
    script.mode = Mode.ADAPTED

    assert not has_errors(check_script(script, book))


def test_validator_span_and_speaker_errors():
    script = _script("# One\n\nGHOST: Boo.\n\nPEGGY: <shout>no</shout> <slow>a <emphasis>b</emphasis></slow> <whisper>open\n")
    errors = [m for s, m in _messages(script, None) if s is Severity.ERROR]

    assert any("GHOST not in cast" in m for m in errors)
    assert any("unknown span <shout>" in m for m in errors)
    assert any("nested" in m for m in errors)
    assert any("never closed" in m for m in errors)


def test_validator_density_warnings():
    script = _script("# One\n\nPEGGY: [sighs] a [laughs] b [gasps] c\n\nNARRATOR: x\n")

    warnings = [m for s, m in _messages(script, None) if s is Severity.WARN]
    assert any("3 event tags" in m for m in warnings)


def test_validator_missing_source_chapter():
    book = Book("T", "A", "en", "s", [Chapter("One", ["A."]), Chapter("Two", ["B."])])
    script = _script("# One\n\nNARRATOR: A.\n\nPEGGY: x\n")
    script.mode = Mode.ADAPTED

    issues = check_script(script, book)
    assert any(i.severity is Severity.WARN and "02 has no script" in i.message for i in issues)


def test_skipped_chapter_is_accepted_and_not_planned():
    book = Book("T", "A", "en", "s", [Chapter("Contents", ["One. Two."]), Chapter("One", ["A."])])
    script = fmt.parse_cast(CAST)
    script.chapters.append(fmt.parse_chapter("<!-- skip: table of contents -->\n# Contents\n", 1, "01.md"))
    script.chapters.append(fmt.parse_chapter("# One\n\nNARRATOR: A.\n\nPEGGY: x\n", 2, "02.md"))
    script.chapters[1].segments.pop()  # keep words identical to the source

    assert not has_errors(check_script(script, book))
    jobs = plan_jobs(script, 400, TagPolicy.KEEP)
    assert [j.subtitle for j in jobs] == ["One", "A."]
    assert jobs[0].boundary is Boundary.START
    assert "Contents" not in fmt.render_full(script)


def test_validator_rejects_reserved_tokens():
    script = _script("# One\n\nNARRATOR: <|speaker:1|> hi\n\nPEGGY: x\n")

    assert any("reserved" in m for _, m in _messages(script, None))


# ---------------------------------------------------------------- planner


def test_plan_carries_mood_per_speaker():
    script = _script('# One\n\nPEGGY: [bright] "Hi."\n\nNARRATOR: She waved.\n\nPEGGY: "Bye."\n\nPEGGY: [sad] "Oh."\n\nPEGGY: "Well."\n')
    jobs = plan_jobs(script, 400, TagPolicy.KEEP)

    assert [j.tts_text for j in jobs] == ["One", '[bright] "Hi."', "She waved.", '[bright] "Bye."', '[sad] "Oh."', '[sad] "Well."']


def test_plan_boundaries():
    script = _script("# One\n\nNARRATOR: A.\n\nNARRATOR: B.\n\nPEGGY: C.\n\n---\n\nNARRATOR: D. E.\n")
    script.chapters.append(fmt.parse_chapter("# Two\n\nNARRATOR: F.\n", 2))
    jobs = plan_jobs(script, 3, TagPolicy.KEEP)

    assert [(j.subtitle, j.boundary) for j in jobs] == [
        ("One", Boundary.START),
        ("A.", Boundary.SEGMENT),
        ("B.", Boundary.SEGMENT),
        ("C.", Boundary.SPEAKER),
        ("D.", Boundary.SCENE),
        ("E.", Boundary.PIECE),
        ("Two", Boundary.CHAPTER),
        ("F.", Boundary.SEGMENT),
    ]


def test_plan_titles_stay_plain_and_split_pieces_repeat_mood():
    script = _script("# One\n\nPEGGY: [icy calm] First sentence here. Second sentence here.\n\nNARRATOR: x\n")
    jobs = plan_jobs(script, 35, TagPolicy.KEEP)

    assert jobs[0].tts_text == "One"
    assert jobs[1].tts_text == "[icy calm] First sentence here."
    assert jobs[2].tts_text == "[icy calm] Second sentence here."
    assert jobs[2].subtitle == "Second sentence here."
