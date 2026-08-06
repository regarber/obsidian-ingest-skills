---
name: video-ingest
description: "Watch a video from any platform and file structured notes into your notes vault. Pulls the transcript, extracts scene-change frames, runs a vision pass over them, then writes one source note plus atomic claim notes in the vault's house style. Triggers on: watch this video, ingest this video, take notes on this talk, summarize this youtube video, process this recording, ingest this zoom recording, notes from this vimeo, transcribe and file, add this video to my vault, watch and file this."
---

# video-ingest

Turn a video into notes that compound. The mechanical half — download, transcript,
frames — is handled by `scripts/prepare_video.py`. Your job is the judgment half:
deciding what is actually worth a note and writing it in the vault's voice.

Sibling skill: `article-ingest` does the same for written sources. Same vault
contract, same note style; only the extraction differs.

## Configuration

Edit this table once, after installing the skill. These are the only
machine-specific values — nothing else in the skill hardcodes a path.

| Setting | Value | What it is |
|---|---|---|
| `VAULT_ROOT` | `~/Documents/MyVault` | Where claim notes are written |
| `SOURCES_DIR` | `<VAULT_ROOT>/Sources` | Where source notes are written |
| `ATTACHMENTS_DIR` | `<VAULT_ROOT>/_attachments` | Where kept frames are copied |
| `PYTHON` | `python3` | Interpreter that has this skill's dependencies |
| `SEARCH_CMD` | *(optional)* | Command that searches the vault — see step 4 |
| `RAW_DIR` | `<VAULT_ROOT>/_raw` | Where the unedited capture is archived — see step 5b |
| `INDEX_CMD` | *(optional)* | Command that reindexes the vault — see step 6 |

`PYTHON` must be the interpreter `faster-whisper` was installed into. If a
virtualenv was used, that is its interpreter — `<venv>/bin/python` on macOS and
Linux, `<venv>\Scripts\python.exe` on Windows — not the bare `python` on PATH.
Dependency errors print the exact install command for whichever interpreter
actually ran the script.

`ffmpeg` and `yt-dlp` are external binaries, resolved from PATH. The script
prints the right install command for the platform if either is missing.

## Before you start

If the source is a **meeting or call recording with other people in it**, say so
and confirm before running. The transcript and frames stay on the machine, but
the vision pass sends frames to the model — that is the one step in this
pipeline that leaves the box. One sentence is enough; do not belabor it. Public
talks and published videos need no such check.

---

## Step 1 — Prepare

```bash
<PYTHON> scripts/prepare_video.py <url-or-path> --work-dir <scratch>/vi-<slug>
```

Invoke it by absolute path — the working directory does not matter. The one
exception: **a relative path to a local video resolves against the current
directory**, so prefer absolute paths for local files.

Useful flags:

- `--scene-threshold 0.30` — lower (0.15) for slide decks where transitions are subtle;
  raise (0.45) for handheld footage that would otherwise trip on every camera move.
- `--max-frames 40` — evenly subsamples if the video overruns the budget.
- `--whisper-model small` — when accuracy matters. The `base` default is fast but
  fumbles technical vocabulary and proper nouns.
- `--force-asr` — ignore platform captions and transcribe locally. Reach for this when
  auto-captions are visibly garbled.
- `--no-frames` — transcript only, for podcasts and audio-first sources.

It prints a JSON summary and writes `meta.json`, `transcript.md`, `transcript.json`,
`frames/`, and `frames.json` into the work dir.

**If it fails**, read the error before retrying. Missing tools report their own install
command. A source with no captions and no speech is a real dead end — say so rather
than filing an empty note.

## Step 1b — Ask the vault what it already knows

Before reading anything, run one search on the title and main topic:

```bash
<SEARCH_CMD> "<title or main topic>"
```

(No `SEARCH_CMD`? Grep `VAULT_ROOT` for the two or three obvious terms instead.)

A few hundred tokens, and it changes how you read. A source on ground the vault
already holds should be read for **deltas** — what is new, sharper, or in
conflict. A source on new ground should be read cold.

This does not replace step 4, which is done properly once you know what the
source actually argues. It stops you paying to read a whole document before
discovering the vault covered it better last week.

## Step 2 — Read the transcript

Read `transcript.md` in full. Not a skim — the claims worth keeping are usually
asides, not the stated thesis. This is the one cost worth paying every time;
the savings elsewhere exist so this one stays affordable.

## Step 3 — Vision pass

Read `frames.json`, then use the Read tool on the frames worth looking at. Do not
open all forty reflexively; the transcript tells you which timestamps had something
on screen worth seeing.

Each entry carries `bytes` and, on runs of ten frames or more, `likely_blank`.
**Skip anything flagged `likely_blank`** — those are the bottom decile by file
size, which at fixed JPEG quality means a fade, a title card, or a near-black
transition. A scene-change detector fires on those exactly as readily as on a
slide, and opening one costs a thousand-plus tokens to learn nothing.

It is a hint, not a verdict. A dark screenshot with real content can sit
mid-pack, so weigh it against what the transcript says was happening.

Look for what the audio cannot carry:

- Diagrams, architecture sketches, and their actual structure
- Code and config on screen (transcribe it — speakers rarely read it aloud)
- Numbers in charts, benchmark tables, pricing
- Slide titles that name a framework the speaker never says out loud
- UI state during a demo

Ignore talking heads, title cards, and transitions. A frame that shows nothing the
transcript already said is not worth a line.

## Step 4 — Check what the vault already knows

New notes should connect to existing ones rather than duplicate them.

With `SEARCH_CMD` configured:

```bash
<SEARCH_CMD> "<topic>"
```

Otherwise, Grep `VAULT_ROOT` for the main terms and read the titles that come
back — filenames alone are informative when notes are titled as claims.

Run it for two or three of the main themes. Use the hits to decide what to wikilink,
and to notice when the video contradicts something already in the vault — a
contradiction is higher-signal than agreement, so write it down explicitly.

## Step 5 — Write the notes

Two kinds of note. Both matter; they do different jobs.

### The source note — `<SOURCES_DIR>/<slug>.md`

Reference material. One per video.

```markdown
---
type: source
media: video
url: <url or null>
channel: <uploader>
published: <YYYY-MM-DD or null>
duration: <H:MM:SS>
transcript_source: <subtitles (vtt) | faster-whisper (base)>
ingested: <YYYY-MM-DD>
---

# Video: <Title>

<Two or three sentences: what this is, who made it, why it was worth watching.>

## Timeline

- **[00:00]** <beat>
- **[04:12]** <beat>

## On screen

- **[04:12]** <what the frame showed that the audio did not>

## Claims filed

- [[First claim title]]
- [[Second claim title]]
```

The source note may run long — the ~200-word rule does not apply to it. It is an
index, not an idea.

### The claim notes — `<VAULT_ROOT>/<Claim title>.md`

This is the part that earns its keep. One note per idea, filed at the vault root
alongside the user's own notes.

```markdown
---
type: claim
source: "[[<slug>]]"
at: "<MM:SS>"
ingested: <YYYY-MM-DD>
---

# <The claim, stated as an assertion>

<~150 words. What the claim is, the reasoning or evidence behind it, and why it
matters. Link [[related notes]] you found in step 4.>

Source: [[<slug>]] at [<MM:SS>](<url>&t=<seconds>s)
```

Non-negotiables:

- **The H1 is the claim.** Retrieval ranks and cites on the first H1, so a vague
  title makes the note unfindable and its citation useless.
  - Good: `Scene-change frame extraction beats fixed-interval sampling for talks`
  - Bad: `Notes on video processing`
- **No `: * ? " < > |` in the filename.** Windows rejects them, and a claim-shaped
  title invites a colon. Use an em dash instead, and keep the H1 identical to the
  filename so wikilinks and citations agree.
- **One idea per note.** Two claims in one note means it matches both queries and
  answers neither.
- **~200 words max.** Longer notes chunk into fragments that lose the title's context.
- **Link liberally.** `[[Wikilinks]]` are what makes the vault navigable, and what
  any resurfacing job traverses later.

How many claim notes: usually **3-8** for a conference talk, **2-4** for a meeting.
Driven by content, not length. A video with one good idea gets one note. Do not
manufacture claims to hit a number — filler notes actively degrade retrieval by
crowding out real hits. **Filing nothing is a valid outcome** when the vault already covers the source better; say so in the report.

### Frames worth keeping

If a frame carries information the text cannot (a diagram, a chart), copy just that
frame to `<ATTACHMENTS_DIR>/video-frames/<slug>/` and embed it:

```markdown
![[video-frames/<slug>/frame-012_04m12.jpg]]
```

Only referenced frames. Unreferenced images are clutter, and most indexers ignore
them anyway.

## Step 5b — Archive the raw capture

Copy the extracted transcript into the vault, under the same `<slug>` as the source
note:

```bash
cp <work-dir>/transcript.md <RAW_DIR>/<slug>.md
```

Why this is not optional: a claim note is **your interpretation** of the source.
The `at:` field makes it checkable — but only while the original is still
reachable, and a deleted post, an unlisted video, or a reorganised site takes the
evidence with it. The work dir is scratch and gets wiped.

**Never edit anything in `<RAW_DIR>`.** Not to fix a typo, not to trim it. A
corrected archive is no longer evidence of what the source said; note the
correction in the source note, where it belongs.

Frames are not archived: they are large, and the transcript plus the source
note's **On screen** section already record what they showed.

If the vault is backed by a retrieval index, exclude `<RAW_DIR>` from it. The
archive exists to be audited, not retrieved — indexing it puts verbose source
text in competition with the notes that distil it.

## Step 6 — Reindex

If `INDEX_CMD` is configured:

```bash
<INDEX_CMD>
```

Skip this step entirely if it is not. A plain Obsidian vault picks up new files
on its own.

## Step 7 — Report

Say what landed: the source note, each claim title, anything that contradicted
existing notes, and anything deliberately skipped. Claim titles are the useful part
of the report — they are what the user will actually search for later.

---

## What not to do

- **Do not summarize the video.** A summary is a worse copy of the transcript. Extract
  claims that stand on their own away from the source.
- **Do not file the transcript into the indexed vault.** The archive is
  `<RAW_DIR>`; the source note carries the timeline. Full text in the index would
  swamp every embedding search.
- **Do not overwrite an existing note** whose title collides. Read it first, then
  either extend it or pick a sharper title for the new claim.
- **Do not invent timestamps.** Every `at:` must come from `transcript.json` or
  `frames.json`.
