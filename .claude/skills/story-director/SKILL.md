---
name: story-director
description: Turn one chapter of a storyaudio draft script into a performance script - attribute every line to a cast voice and add Drama 3 voice direction tags - without changing the author's words. Use for each work/<id>/script/NN-*.md file during the storyaudio workflow, or when the user asks to voice-tag, cast, or direct a chapter.
---

# Story director: one chapter

Input: `script/00-cast.md`, one `script/NN-*.md` (the draft), and the same
chapter in `work/<id>/source.md`.
Output: the same `NN-*.md`, rewritten in place, that passes `storyaudio check`.

Tag rules come from `docs/reference/voice-direction.md`.
Read that file first. This skill adds only what the script format needs.

## Script format

    # Chapter Title

    NARRATOR: [warm, quietly wry] Four years later...

    PEGGY: [bright, a little breathless] "Morning."

    NARRATOR: she said, and climbed into the Rover.

    ---

    NARRATOR: The next scene starts here.

- `SPEAKER_ID: text` starts a segment. A blank line ends it. Each segment
  is one or more Drama 3 requests in that speaker's cloned voice.
- `# Title`: the chapter heading. NARRATOR reads it. No tags on it.
- `---`: scene break (1.2 s pause). Keep the source's breaks.
- `<!-- ... -->`: notes for humans. Not spoken.

## Step 1: attribute

Read the chapter. For each quotation, find who speaks. Use the attribution
("said Oliver"), the turn order, the vocatives ("Morning, Peggy"), and the
context. Then split the paragraph:

    Source:  "Morning, Peggy," the Reverend greeted her. "Cold today."

    OLIVER: "Morning, Peggy,"

    NARRATOR: the Reverend greeted her.

    OLIVER: "Cold today."

Rules:
1. Quotation marks stay inside the character's segment.
2. Narration between quotes goes to NARRATOR, even "he said".
3. Quoted text that is not speech (signs, letters, thoughts in quotes)
   stays with NARRATOR, unless the text says it is read aloud.
4. Speech you cannot attribute with confidence stays with NARRATOR. Wrong
   voices are worse than the narrator.
5. Keep every word, in order, exactly once. Punctuation may change only to
   place a tag. `check` compares words, not punctuation.
6. Single-narrator form: do not split. NARRATOR reads all; use mood tags
   to color each character's line.

## Step 2: direct

Apply the tagger rules from `docs/reference/voice-direction.md`. Short form:

| Tag | Form | Use |
| --- | --- | --- |
| Mood | `[icy calm]` at segment start | when emotion changes; it stays active for that speaker |
| Event | `[laughs] [chuckles] [sighs] [gasps] [pause] [clears throat] [hesitates]` | only where the text shows the action; max 2 per segment |
| Span | `<whisper>..</whisper> <slow>..</slow> <emphasis>..</emphasis>` | 1-5 words, never nested, max 2 per segment |

Script-specific rules:
- A mood stays active per speaker across segments in the chapter. The
  renderer sends it again on each request. Do not repeat a mood that is
  already active.
- Every speaker's first segment in a chapter gets a mood tag.
- NARRATOR gets a base mood per scene. Change it when the scene changes.
- Moods describe sound (tone, pace, volume, breath), not plot:
  `[low, tense, slow]`, not `[suspicious of the vicar]`.
- Use `[pause]` before a reveal or a chapter's last line. Do not add
  pauses between segments: the renderer adds gaps there.

## Step 3: verify

1. Run `storyaudio check BOOK`. Fix every ERROR for this chapter.
2. Fix WARN lines unless you have a reason. Write the reason in a
   `<!-- -->` note.
3. Read the chapter top to bottom as a listener. Check that each character
   keeps one voice and each scene has a mood.

## Example

Source paragraph:

    "Are you going to tell me that you were making some anyroad, and it was no fuss at all?" he asked cheekily.

Script:

    OLIVER: [teasing, warm] "Are you going to tell me that you were making some anyroad, and it was no fuss at all?"

    NARRATOR: he asked cheekily.
