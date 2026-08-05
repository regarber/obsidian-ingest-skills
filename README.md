# obsidian-ingest-skills

Two [Claude Code](https://claude.com/claude-code) skills that turn sources into
atomic, linked notes in a Markdown vault — one for written material, one for
video.

They are not summarizers. A summary is a worse copy of the source, and it rots
in a vault because nothing ever links to it. These skills extract **claims**:
one idea per note, titled as an assertion, carrying a citation back to the exact
page or timestamp it came from.

| Skill | Handles |
|---|---|
| `article-ingest` | Web URLs, PDFs (papers and scans), `.docx`, `.epub`, saved HTML, Markdown, plain text |
| `video-ingest` | Anything `yt-dlp` can reach, plus local video and audio files |

## What you get per source

- **One source note** in `Sources/` — an index of the argument, with a structure
  or timeline section and links to every claim filed from it.
- **Three to eight claim notes** at the vault root, each ~150 words, each titled
  as a claim, each citing `p. 7` or `[04:12]` so it stays checkable.
- **Figures and frames worth keeping**, copied into the vault and embedded. Only
  the ones actually referenced.

## Why claims, not summaries

The note title is what search ranks on and what a citation displays. A note
called `Notes on retrieval` matches nothing useful and cites nothing useful. A
note called `Vector search alone drifts on short queries` is findable a year
later by someone who has forgotten the source entirely — which is the whole
point of writing it down.

The rest of the house style follows from that: one idea per note (two claims in
one note matches both queries and answers neither), ~200 words (longer notes
chunk into fragments that lose the title's context), and wikilinks everywhere.

## Install

Copy or symlink each skill directory into your Claude Code skills folder:

```
~/.claude/skills/article-ingest/
~/.claude/skills/video-ingest/
```

Then install the dependencies and **edit the Configuration table at the top of
each `SKILL.md`** — those tables are the only machine-specific values in the
repo.

### Python dependencies

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt      # Windows: .venv\Scripts\pip
```

Point `PYTHON` in both Configuration tables at that venv's interpreter. Every
dependency error prints the exact install command for whichever interpreter
actually ran the script, so a mismatch is self-diagnosing.

Everything in `requirements.txt` is optional in the sense that it is only
imported when the matching source type shows up — you can skip `ebooklib` if you
never ingest EPUBs. The scripts tell you what is missing when it is missing.

### External binaries — `video-ingest` only

| Tool | Windows | macOS | Linux |
|---|---|---|---|
| ffmpeg | `winget install Gyan.FFmpeg` | `brew install ffmpeg` | `apt install ffmpeg` |
| yt-dlp | `winget install yt-dlp.yt-dlp` | `brew install yt-dlp` | `apt install yt-dlp` |

On Windows the script also resolves these out of winget's package directories,
because winget updates the persisted PATH but an already-running shell keeps the
environment it started with — so a freshly installed tool would otherwise be
invisible until the next session.

## Configuration

Both skills read a small table at the top of their `SKILL.md`:

| Setting | Required | What it is |
|---|---|---|
| `VAULT_ROOT` | yes | Where claim notes are written |
| `SOURCES_DIR` | yes | Where source notes are written |
| `ATTACHMENTS_DIR` | yes | Where kept figures and frames are copied |
| `PYTHON` | yes | Interpreter holding the dependencies |
| `SEARCH_CMD` | no | Command that searches the vault before writing |
| `INDEX_CMD` | no | Command that reindexes the vault after writing |

`SEARCH_CMD` and `INDEX_CMD` exist for vaults backed by a retrieval system. Leave
them empty for a plain Obsidian vault — the search step falls back to Grep, and
the reindex step is skipped.

## How it is split

The Python scripts do the mechanical half and nothing else: resolve the source,
extract text with citable position markers, pull the frames or pages whose
meaning lives in pixels rather than words. They write to a work directory and
**never touch the vault**.

Everything requiring judgment — which claims are worth keeping, how to phrase
one, what to link it to — stays in `SKILL.md`, executed by the model. That split
is deliberate: the mechanical half is testable and boring, and the judgment half
is the part that cannot be automated into a script without becoming a worse
summarizer.

### `prepare_article.py`

Fetches with a browser user-agent (default library agents get 403s from a lot of
publishers), detects a URL that points straight at a PDF by content type, and
extracts with trafilatura tuned for recall — a missed paragraph is worse than a
stray nav line, because the model can ignore boilerplate but cannot recover lost
text.

For PDFs it recovers the title by typography rather than position (the first
line is often a license stamp; the title is the largest type on the page),
stamps `<!-- page N -->` markers so claims stay citable, and renders only the
pages where text extraction cannot carry the meaning: embedded images, pages
with almost no text layer, and vector charts, which extract as a scatter of bare
axis labels.

It flags thin extractions and paywall stubs rather than letting you write notes
from a login wall.

### `prepare_video.py`

Prefers platform captions over ASR (free, exact, and better), falling back to
`faster-whisper` locally when there are none. YouTube's rolling auto-captions
repeat the tail of each cue so the text appears to scroll; those are collapsed
back into readable prose before anything downstream sees them.

Frames come from ffmpeg scene detection with a periodic floor, because scene
detection alone has a blind spot: a fixed camera pointed at a whiteboard never
produces an abrupt cut, so a visually rich talk can yield a single frame.
Over-budget frame sets are subsampled evenly rather than truncated, so late
material still appears.

## Privacy

The scripts run locally. The only step that sends data anywhere is the vision
pass, where rendered pages or extracted frames go to the model along with the
rest of your conversation.

`video-ingest` therefore asks for confirmation before processing a meeting or
call recording with other people in it. That check is in `SKILL.md` and is worth
keeping.

## License

MIT — see [LICENSE](LICENSE).
