---
name: article-ingest
description: "Ingest an article or document into your notes vault. Handles web URLs, PDFs (including papers and scans), Word documents, EPUBs, saved HTML, and plain text. Extracts clean text, runs a vision pass over figures and charts, then writes one source note plus atomic claim notes in the vault's house style. Triggers on: ingest this article, read this and file it, add this to my vault, take notes on this paper, summarize this pdf, process this document, file this link, ingest this url, read this docx, notes from this book chapter, save this article."
---

# article-ingest

Turn a written source into notes that compound. The mechanical half — fetching,
extracting, rendering figures — is handled by `scripts/prepare_article.py`. Your
job is the judgment half: deciding what is actually worth a note and writing it
in the vault's voice.

Sibling skill: `video-ingest` does the same for video. Same vault contract, same
note style; only the extraction differs.

## Configuration

Edit this table once, after installing the skill. These are the only
machine-specific values — nothing else in the skill hardcodes a path.

| Setting | Value | What it is |
|---|---|---|
| `VAULT_ROOT` | `~/Documents/MyVault` | Where claim notes are written |
| `SOURCES_DIR` | `<VAULT_ROOT>/Sources` | Where source notes are written |
| `ATTACHMENTS_DIR` | `<VAULT_ROOT>/_attachments` | Where kept figures are copied |
| `PYTHON` | `python3` | Interpreter that has this skill's dependencies |
| `SEARCH_CMD` | *(optional)* | Command that searches the vault — see step 4 |
| `INDEX_CMD` | *(optional)* | Command that reindexes the vault — see step 6 |

`PYTHON` must be the interpreter the dependencies were installed into. If a
virtualenv was used, that is its interpreter — `<venv>/bin/python` on macOS and
Linux, `<venv>\Scripts\python.exe` on Windows — not the bare `python` on PATH.
Every dependency error from the script prints the exact install command for
whichever interpreter actually ran it.

`SEARCH_CMD` and `INDEX_CMD` are for vaults backed by a retrieval system. Leave
them empty for a plain Obsidian vault; the steps below degrade to Grep, which
works everywhere.

---

## Step 1 — Prepare

```bash
<PYTHON> scripts/prepare_article.py <url-or-path> --work-dir <scratch>/ai-<slug>
```

Invoke the script by absolute path; the working directory does not matter. The
one exception: **a relative path to a local file resolves against the current
directory**, so prefer absolute paths for files.

Handles web URLs, `.pdf`, `.docx`, `.epub`, `.html`, `.md`, `.txt` — and a URL
that points straight at a PDF is detected by content type and processed as a
document, not scraped as a page.

Useful flags:

- `--max-render 12` — cap on PDF pages rendered for the vision pass.
- `--no-vision` — text only; skip page rendering entirely.

It writes `meta.json`, `article.md`, `pages/`, and `pages.json`, then prints a
JSON summary.

### If the summary says `needs_browser: true`

The fetch returned a paywall stub, a login wall, or a JavaScript shell rather
than an article. Check `thin_extraction` and `paywall_suspected` to see which.

If a browser automation tool is available and the user is logged in to the site,
fall back to it:

1. Open the URL in a tab and read the rendered article text.
2. Save it to `<work-dir>/browser.md` with an `# H1` title line.
3. Re-run `prepare_article.py` on that file and continue from step 2.

**Only for content the user has legitimate access to.** If a site is genuinely
paywalled and they have no subscription, say so and stop — do not look for a way
around it.

## Step 2 — Read the article

Read `article.md` in full. Not a skim.

Note the position markers, which are what make a claim checkable later:

- **PDFs** carry `<!-- page N -->` before each page → cite as `p. 7`
- **EPUBs** carry `<!-- chapter: name -->` → cite the chapter
- **Web and DOCX** keep their heading structure → cite the section heading

## Step 3 — Vision pass

Read `pages.json`. Each entry says *why* the page was rendered:

- `embedded image` — a figure, photo, or screenshot
- `vector graphics (chart or diagram)` — a plotted chart or drawn diagram
- `little or no text layer` — a scanned page, or a full-bleed figure

Use the Read tool on the pages worth looking at — not all of them reflexively.
The text usually tells you which figures carry the argument.

Look for what the text cannot carry:

- What a diagram's structure actually asserts (arrows, nesting, feedback loops)
- Numbers and trends in charts, which extract as bare axis labels or not at all
- Table structure, which flattens into an unreadable run of cells
- Anything on a scanned page, where the render *is* the only content

If `has_text_layer` is `false`, the document is a scan: the rendered pages are
the source, and the extracted text is noise. Transcribe from the images.

Note that this step sends rendered pages to the model. For a public article that
is unremarkable; for a private or confidential document, see the note in
`video-ingest` about confirming first.

## Step 4 — Check what the vault already knows

New notes should connect to existing ones rather than duplicate them.

With `SEARCH_CMD` configured:

```bash
<SEARCH_CMD> "<topic>"
```

Otherwise, Grep `VAULT_ROOT` for the main terms and read the titles that come
back — filenames alone are informative when notes are titled as claims.

Run it for two or three main themes. Use the hits to decide what to wikilink,
and to notice contradictions with existing notes — a contradiction is
higher-signal than agreement, so write it down explicitly rather than smoothing
it over.

## Step 5 — Write the notes

Two kinds. Both matter; they do different jobs.

### The source note — `<SOURCES_DIR>/<slug>.md`

Reference material. One per document.

```markdown
---
type: source
media: article
url: <url or null>
source_path: <local path, if a file>
author: <author>
published: <YYYY-MM-DD or null>
source_type: <web | pdf | pdf (web) | docx | epub | html | text>
word_count: <n>
ingested: <YYYY-MM-DD>
---

# Article: <Title>

<Two or three sentences: what this is, who wrote it, why it was worth reading.>

## Structure

- **<section or p. N>** <the beat of the argument there>

## Figures

- **p. 3** <what the figure shows that the prose does not>

## Claims filed

- [[First claim title]]
- [[Second claim title]]
```

The source note may run long. It is an index, not an idea.

### The claim notes — `<VAULT_ROOT>/<Claim title>.md`

This is the part that earns its keep. One note per idea, at the vault root
alongside the user's own notes.

```markdown
---
type: claim
source: "[[<slug>]]"
at: "<p. 7 | section name | chapter>"
ingested: <YYYY-MM-DD>
---

# <The claim, stated as an assertion>

<~150 words. What the claim is, the reasoning or evidence behind it, and why it
matters. Link [[related notes]] you found in step 4.>

Source: [[<slug>]], <p. 7 or section name>
```

Non-negotiables:

- **The H1 is the claim.** Retrieval ranks and cites on the first H1, so a vague
  title makes the note unfindable and its citation useless.
  - Good: `Vector search alone drifts on short queries`
  - Bad: `Notes on retrieval`
- **No `: * ? " < > |` in the filename.** Windows rejects them and claim-shaped
  titles invite a colon. Use an em dash, and keep the H1 identical to the
  filename so wikilinks and citations agree.
- **One idea per note.** Two claims in one note means it matches both queries
  and answers neither.
- **~200 words max.** Longer notes chunk into fragments that lose the title's
  context.
- **Link liberally.** `[[Wikilinks]]` are what makes the vault navigable, and
  what any resurfacing job traverses later.

How many claim notes: usually **3-8** for a long-form article or paper, **2-5**
for a blog post, **1-3** for a short piece. Driven by content, not length. Do
not manufacture claims to hit a number — filler notes degrade retrieval by
crowding out real hits.

### Figures worth keeping

If a figure carries information the text cannot, copy that rendered page to
`<ATTACHMENTS_DIR>/article-figures/<slug>/` and embed it:

```markdown
![[article-figures/<slug>/page-003.png]]
```

Only referenced figures. Unreferenced images are clutter, and most indexers
ignore them anyway.

## Step 6 — Reindex

If `INDEX_CMD` is configured:

```bash
<INDEX_CMD>
```

Skip this step entirely if it is not. A plain Obsidian vault picks up new files
on its own.

## Step 7 — Report

Say what landed: the source note, each claim title, anything that contradicted
existing notes, and anything deliberately skipped. The claim titles are the
useful part — they are what the user will search for later.

---

## What not to do

- **Do not summarize the article.** A summary is a worse copy of the source.
  Extract claims that stand on their own away from it.
- **Do not file the full text into the vault.** It lives in the work dir. The
  source note carries the structure; the full text would swamp every search.
- **Do not overwrite an existing note** whose title collides. Read it first,
  then either extend it or pick a sharper title.
- **Do not invent page numbers.** Every `at:` must come from a position marker
  in `article.md` or an entry in `pages.json`.
- **Do not trust a thin extraction.** If `thin_extraction` is true, you are
  probably reading a paywall stub, not the article. Check before writing notes
  from it.
- **Do not skip step 6** if an index command is configured. An unindexed note
  does not exist.
