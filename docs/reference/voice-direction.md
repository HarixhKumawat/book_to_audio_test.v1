---
name: voice-direction-tagger
description: Add voice direction tags to story prose or dialogue for narration or TTS. Use when the user gives text and asks for voice-acting tags.
---

# Voice Direction Tagger

Adds tags showing the emotion the text implies. Never rewrites the story.

## Tag types

Tags in [ ] or < > are direction, not spoken.

| Type            | Form                       |
| --------------- | -------------------------- |
| Event           | [laughs]                   |
| Span            | <whisper>text</whisper>    |
| Mood            | [icy calm] text            |
| Director's note | Direction: ... / Line: ... |

## Vocabulary

**Events** (use only these)
laughs, chuckles, sighs, gasps, pause, clears throat, hesitates

**Spans** (use only these)
whisper, slow, emphasis

**Moods** (examples; any wording allowed, invent when none fit, e.g. "tired, quietly wry")

- with bright eager excitement
- icy calm
- heavy, deflated sadness
- sarcastic and deadpan
- breathless and terrified
- furious, barely holding it together
- warm, gentle bedtime voice

## Rules

1. **Keep the text.** No words added, removed, or changed. Change punctuation only to place a tag, and report each change.
2. **Mood tags only where emotion changes**, at paragraph or line start. A mood stays active until the next one.
3. **Tag narration and dialogue.** Narration gets a base mood for the scene; each speaker gets a mood for the line.
4. **One base mood for the main character.** Shift only when another character or event causes it. Relief and humor are small lifts, never unearned cheer.
5. **Event tags at the moment of action.** [pause] before a key line or after "she said"; [sighs] after a stated sigh; [chuckles] (small) or [laughs] (full) after a stated laugh; [hesitates] before broken lines. Only when the text supports it, one tag per action.
6. **Span tags on 1-5 words, never nested.** emphasis for the key word, slow for careful speech, whisper for private or guilty remarks.
7. **Broken speech:** keep the dash, add [pause] or [hesitates], and put any new mood after the break.
8. **Density:** max 2 event tags per paragraph and 2 span tags per line. Leave plain text between tags.
9. **Tag only what a voice can do** (tone, speed, volume, breath, pause, laugh), not thoughts, images, or plot.
10. **Leave headings plain.** The first mood tag goes on the first body sentence.
11. **Make the last line land:** [pause] before it if it's a question, offer, or hook, plus slow or emphasis on its key words.

## Procedure

Read once untagged, find scene and character moods, place mood tags, add event tags, add span tags, trim density, then compare with the original to confirm identical words.

## Output

1. One sentence saying what you did.
2. Horizontal rule.
3. Tagged text.
4. Horizontal rule.
5. Up to 3 notes on main choices, including punctuation changes.
6. One sentence offering a change: different density, one narrator voice, or director's-note version.

## Example

Input:

    "Sister Margaret," a low male voice called her.
    "It's not 'sister' anymore," she said.

Output:

    [warm, gentle] "Sister Margaret," a low male voice called her.
    [tired, quietly wry] "It's not 'sister' anymore," she said. [pause] "Hello, Oliver."
