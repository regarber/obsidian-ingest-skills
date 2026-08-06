"""Prepare a video for ingestion into a notes vault.

Does the mechanical half of the pipeline and nothing else: resolve the source,
get a timestamped transcript, pull scene-change frames. Judgment -- what is
worth a note, how to phrase the claim -- stays with Claude, which reads this
script's output.

This script never touches the vault. Every artifact lands in the work
directory; SKILL.md governs what gets written into the vault and where.

Usage:
    python prepare_video.py <url-or-path> [options]

Writes a work directory containing meta.json, transcript.json, transcript.md,
frames/, and frames.json, then prints a JSON summary to stdout.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

IS_WINDOWS = os.name == "nt"

# winget updates the persisted user PATH, but an already-running shell keeps the
# environment it started with -- so a freshly installed tool is invisible until
# the next session. Resolve against winget's own directories rather than making
# the first run after an install fail confusingly. Windows-only: on POSIX an
# empty LOCALAPPDATA would turn these into relative paths that could match
# something in the current directory.
_LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA") or "~nonexistent~")
WINGET_LINKS = _LOCALAPPDATA / "Microsoft" / "WinGet" / "Links"
WINGET_PACKAGES = _LOCALAPPDATA / "Microsoft" / "WinGet" / "Packages"

# Install hints, per platform. A wrong command is worse than no command, so
# these are only as specific as the platform allows.
if IS_WINDOWS:
    INSTALL_HINTS = {
        "ffmpeg": "winget install Gyan.FFmpeg",
        "yt-dlp": "winget install yt-dlp.yt-dlp",
    }
elif sys.platform == "darwin":
    INSTALL_HINTS = {
        "ffmpeg": "brew install ffmpeg",
        "yt-dlp": "brew install yt-dlp",
    }
else:
    INSTALL_HINTS = {
        "ffmpeg": "sudo apt install ffmpeg   (or your distro's equivalent)",
        "yt-dlp": "sudo apt install yt-dlp   (or: pipx install yt-dlp)",
    }

# A Windows console is cp1252, and Python gives stdout a *strict* error handler
# (unlike stderr, which defaults to backslashreplace). One emoji in a video title
# would otherwise raise UnicodeEncodeError on the final summary print -- after
# every artifact had already been written correctly, so a total success looked
# like a crash.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
except (AttributeError, ValueError):  # stdout replaced by a non-TextIO object
    pass

VIDEO_SUFFIXES = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".wmv", ".flv", ".mpg", ".mpeg"}
AUDIO_SUFFIXES = {".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg", ".opus", ".wma"}


def die(msg: str, code: int = 1) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def die_missing(package: str, why: str = "") -> None:
    """Exit with an install command naming the interpreter that is running.

    `sys.executable` beats a documented path: whichever python invoked this
    script is the one that needs the package, which is the whole of the fix in
    the common failure -- a venv exists but the script was run with the system
    python, or vice versa.
    """
    detail = f" {why}" if why else ""
    die(f"{package} is not installed.{detail}\n"
        f"Install it with:\n"
        f"  {sys.executable} -m pip install {package}")


def find_tool(name: str) -> str | None:
    """Resolve an executable: PATH first, then winget's shim and package dirs."""
    found = shutil.which(name)
    if found:
        return found

    if not IS_WINDOWS:
        return None

    candidate = WINGET_LINKS / f"{name}.exe"
    if candidate.exists():
        return str(candidate)

    if WINGET_PACKAGES.is_dir():
        # Package layouts vary (some ship a bin/, some don't), so glob both
        # shapes and prefer the newest build when a tool appears twice.
        matches = list(WINGET_PACKAGES.glob(f"*/{name}.exe"))
        matches += list(WINGET_PACKAGES.glob(f"*/*/{name}.exe"))
        matches += list(WINGET_PACKAGES.glob(f"*/*/bin/{name}.exe"))
        if matches:
            return str(max(matches, key=lambda p: p.stat().st_mtime))

    return None


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw
    )


def slugify(text: str, maxlen: int = 60) -> str:
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE).strip().lower()
    text = re.sub(r"[\s_-]+", "-", text)
    return text[:maxlen].strip("-") or "video"


def hhmmss(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


# --------------------------------------------------------------------------
# Transcript parsing
# --------------------------------------------------------------------------

TIMING_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[.,](\d{3})"
)
# YouTube auto-captions embed per-word karaoke timing and styling tags.
INLINE_TAG_RE = re.compile(r"<[^>]+>")


def _ts(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_subtitle_file(path: Path) -> list[dict]:
    """Parse a .vtt or .srt file into [{start, end, text}].

    Handles the two things that make real-world caption files messy: inline
    styling tags, and YouTube's rolling auto-captions, where each cue repeats
    the tail of the previous one so the text appears to scroll.
    """
    raw = path.read_text(encoding="utf-8", errors="replace")
    segments: list[dict] = []

    for block in re.split(r"\n\s*\n", raw):
        match = TIMING_RE.search(block)
        if not match:
            continue
        start = _ts(*match.group(1, 2, 3, 4))
        end = _ts(*match.group(5, 6, 7, 8))

        lines = []
        for line in block.split("\n"):
            if TIMING_RE.search(line) or line.strip().upper().startswith("WEBVTT"):
                continue
            if re.fullmatch(r"\d+", line.strip()):  # SRT cue number
                continue
            # Caption files carry HTML entities (&nbsp;, &amp;#39;) that would
            # otherwise survive into the notes and into the embeddings.
            cleaned = html.unescape(INLINE_TAG_RE.sub("", line))
            cleaned = cleaned.replace("\xa0", " ").strip()
            if cleaned:
                lines.append(cleaned)
        text = " ".join(lines).strip()
        if text:
            segments.append({"start": start, "end": end, "text": text})

    return dedupe_rolling(segments)


def dedupe_rolling(segments: list[dict]) -> list[dict]:
    """Collapse YouTube-style rolling captions into non-repeating segments.

    Each auto-caption cue tends to restate the previous cue's text with a few
    new words appended. Keeping only the novel suffix turns that back into
    readable prose instead of a stutter.
    """
    out: list[dict] = []
    for seg in segments:
        text = re.sub(r"\s+", " ", seg["text"]).strip()
        if not text:
            continue
        if out:
            prev = out[-1]["text"]
            if text == prev:
                out[-1]["end"] = seg["end"]
                continue
            if text.startswith(prev):  # pure extension of the previous cue
                out[-1]["text"] = text
                out[-1]["end"] = seg["end"]
                continue
            # Partial overlap: strip the longest prefix of `text` that is a
            # suffix of `prev`.
            overlap = min(len(prev), len(text))
            while overlap > 0 and not prev.endswith(text[:overlap]):
                overlap -= 1
            if overlap > 12:
                text = text[overlap:].strip()
                if not text:
                    out[-1]["end"] = seg["end"]
                    continue
        out.append({"start": seg["start"], "end": seg["end"], "text": text})
    return out


def merge_segments(segments: list[dict], window: float = 30.0) -> list[dict]:
    """Group fine-grained cues into ~`window`-second paragraphs for readability."""
    merged: list[dict] = []
    for seg in segments:
        if merged and seg["end"] - merged[-1]["start"] <= window:
            merged[-1]["text"] += " " + seg["text"]
            merged[-1]["end"] = seg["end"]
        else:
            merged.append(dict(seg))
    return merged


# --------------------------------------------------------------------------
# Source acquisition
# --------------------------------------------------------------------------


def probe_local(path: Path, ffprobe: str | None) -> dict:
    meta = {
        "title": path.stem,
        "source": str(path),
        "source_type": "local",
        "url": None,
        "uploader": None,
        "upload_date": None,
        "duration": None,
    }
    if not ffprobe:
        return meta
    proc = run([ffprobe, "-v", "quiet", "-print_format", "json", "-show_format", str(path)])
    if proc.returncode == 0:
        try:
            fmt = json.loads(proc.stdout).get("format", {})
            if fmt.get("duration"):
                meta["duration"] = float(fmt["duration"])
            tags = fmt.get("tags", {})
            for key in ("title", "TITLE"):
                if tags.get(key):
                    meta["title"] = tags[key]
                    break
        except (json.JSONDecodeError, ValueError, TypeError):
            pass
    return meta


def find_sibling_subtitles(path: Path) -> Path | None:
    """Zoom and most recorders drop a caption file beside the recording."""
    stem = path.stem.lower()
    for candidate in sorted(path.parent.glob("*")):
        if candidate.suffix.lower() not in {".vtt", ".srt"}:
            continue
        cand_stem = candidate.stem.lower()
        # Zoom names its caption file e.g. "meeting.transcript.vtt".
        if cand_stem == stem or cand_stem.startswith(stem):
            return candidate
    return None


def pick_subtitle(work: Path) -> Path | None:
    """Choose the best caption track yt-dlp fetched.

    A single video often yields several: `source.en.vtt` (the real track) plus
    machine-translated variants like `source.en-en.vtt`. Prefer a plain language
    code, since translated tracks are a lossy round-trip.
    """
    subs = sorted(work.glob("source*.vtt")) + sorted(work.glob("source*.srt"))
    if not subs:
        return None
    plain = [p for p in subs if "-" not in p.name.split(".")[-2]]
    return (plain or subs)[0]


def fetch_remote(url: str, work: Path, ytdlp: str, want_video: bool,
                 ffmpeg: str | None, force_asr: bool = False
                 ) -> tuple[dict, Path | None, Path | None]:
    """Return (metadata, media_path_or_None, subtitle_path_or_None)."""
    proc = run([ytdlp, "-J", "--no-warnings", "--no-playlist", url])
    if proc.returncode != 0:
        die(f"yt-dlp could not read {url}\n{proc.stderr.strip()[:1500]}")

    info = json.loads(proc.stdout)
    meta = {
        "title": info.get("title") or "Untitled",
        "source": url,
        "source_type": info.get("extractor_key", "remote").lower(),
        "url": info.get("webpage_url") or url,
        "uploader": info.get("uploader") or info.get("channel"),
        "upload_date": info.get("upload_date"),
        "duration": info.get("duration"),
        "description": (info.get("description") or "")[:2000] or None,
    }

    stem = work / "source"

    # yt-dlp shells out to ffmpeg to merge separate video and audio streams.
    # It resolves ffmpeg from PATH, which fails right after a winget install,
    # and the failure is quiet: both streams land but no merged file appears.
    ff_args = ["--ffmpeg-location", str(Path(ffmpeg).parent)] if ffmpeg else []

    # Subtitles first: free, exact, and far better than ASR when they exist.
    run([
        ytdlp, "--skip-download", "--write-subs", "--write-auto-subs",
        "--sub-langs", "en.*,en", "--sub-format", "vtt/srt/best",
        "--no-playlist", "-o", str(stem), url,
    ])
    sub_path = pick_subtitle(work)

    # A previous run in the same work dir can leave partial downloads behind,
    # and yt-dlp keeps per-stream fragments (media.f251.webm) next to the
    # merged result -- clear both so selection below is unambiguous.
    for stale in work.glob("media.*"):
        stale.unlink(missing_ok=True)

    def downloaded(video_only: bool) -> Path | None:
        """Pick the usable media file from whatever yt-dlp left behind.

        Fragments are named `media.f<format_id>.<ext>`, so only the merged
        output has the bare stem `media`. If a merge did not happen, the
        largest matching fragment still carries the picture, which is all the
        frame pass needs.
        """
        files = list(work.glob("media.*"))
        merged = [p for p in files if p.stem == "media"]
        if merged:
            return merged[0]
        wanted = VIDEO_SUFFIXES if video_only else VIDEO_SUFFIXES | AUDIO_SUFFIXES
        frags = [p for p in files if p.suffix.lower() in wanted]
        return max(frags, key=lambda p: p.stat().st_size) if frags else None

    media_path = None
    if want_video:
        # 480p is plenty for slide and diagram legibility, and downloads fast.
        fmt = "bv*[height<=480]+ba/b[height<=480]/bv*+ba/b"
        proc = run([
            ytdlp, "-f", fmt, "--no-playlist", "--merge-output-format", "mp4",
            *ff_args, "-o", str(work / "media.%(ext)s"), url,
        ])
        if proc.returncode != 0:
            print(f"WARNING: video download failed, continuing without frames:\n"
                  f"{proc.stderr.strip()[:800]}", file=sys.stderr)
        else:
            media_path = downloaded(video_only=True)
    elif not sub_path or force_asr:
        # No frames wanted, but ASR still needs audio -- either because the
        # source shipped no captions, or because the caller distrusts them.
        run([
            ytdlp, "-f", "ba/b", "--no-playlist", *ff_args,
            "-o", str(work / "media.%(ext)s"), url,
        ])
        media_path = downloaded(video_only=False)

    return meta, media_path, sub_path


# --------------------------------------------------------------------------
# ASR
# --------------------------------------------------------------------------


def transcribe(media: Path, ffmpeg: str, model_size: str) -> list[dict]:
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        die_missing(
            "faster-whisper",
            "No subtitles were found for this source, so it needs local "
            "transcription.",
        )

    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "audio.wav"
        proc = run([
            ffmpeg, "-y", "-i", str(media), "-vn",
            "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav),
        ])
        if proc.returncode != 0 or not wav.exists():
            die(f"ffmpeg could not extract audio:\n{proc.stderr.strip()[:1500]}")

        print(f"  transcribing with faster-whisper ({model_size}, int8/cpu)...",
              file=sys.stderr)
        # int8 on CPU is the portable default: it keeps the memory footprint
        # small enough to run alongside other work and needs no GPU. With
        # CUDA available, device="cuda" and compute_type="float16" are
        # substantially faster.
        model = WhisperModel(model_size, device="cpu", compute_type="int8")
        segments, _ = model.transcribe(str(wav), vad_filter=True)
        return [
            {"start": s.start, "end": s.end, "text": s.text.strip()}
            for s in segments
            if s.text.strip()
        ]


# --------------------------------------------------------------------------
# Frames
# --------------------------------------------------------------------------

PTS_RE = re.compile(r"pts_time:([0-9.]+)")


def detect_scene_times(media: Path, ffmpeg: str, threshold: float) -> list[float]:
    """Return timestamps where the picture changes abruptly.

    ffmpeg's scene filter scores each frame against its predecessor; `showinfo`
    reports the presentation timestamp of everything that survives. Decoding to
    the null muxer keeps this a measurement pass -- no images written yet.
    """
    proc = run([
        ffmpeg, "-i", str(media),
        "-vf", f"select='gt(scene\\,{threshold})',showinfo",
        "-f", "null", "-",
    ])
    return sorted(float(t) for t in PTS_RE.findall(proc.stderr))


def plan_timestamps(scene_ts: list[float], duration: float | None,
                    max_frames: int) -> list[float]:
    """Decide which moments to capture.

    Scene detection alone has a blind spot: a fixed camera pointed at a
    whiteboard or a slowly-built slide never produces an abrupt cut, so a
    visually rich talk can yield a single frame. Guarantee a floor of periodic
    coverage and let scene cuts add precision on top of it.
    """
    picked = [0.0] + scene_ts

    if duration and duration > 0:
        # Roughly one frame per minute, which keeps a 40-frame budget useful
        # out to about 40 minutes before subsampling kicks in.
        floor = max(2, int(duration // 60))
        floor = min(floor, max_frames)
        if len(picked) < floor:
            step = duration / (floor + 1)
            picked += [step * (i + 1) for i in range(floor)]

    # Collapse near-duplicates: a scene cut and an interval tick landing within
    # a few seconds of each other show the same thing.
    picked.sort()
    deduped: list[float] = []
    for ts in picked:
        if not deduped or ts - deduped[-1] >= 3.0:
            deduped.append(ts)

    # Subsample evenly rather than truncating, so late material still appears.
    if len(deduped) > max_frames:
        step = len(deduped) / max_frames
        deduped = [deduped[int(i * step)] for i in range(max_frames)]

    return deduped


def extract_frames(media: Path, out_dir: Path, ffmpeg: str, threshold: float,
                   max_frames: int, duration: float | None) -> list[dict]:
    """Capture one JPEG per planned timestamp."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("*.jpg"):
        stale.unlink()

    scene_ts = detect_scene_times(media, ffmpeg, threshold)
    timestamps = plan_timestamps(scene_ts, duration, max_frames)
    print(f"  {len(scene_ts)} scene cuts -> capturing {len(timestamps)} frames",
          file=sys.stderr)

    frames = []
    for idx, ts in enumerate(timestamps, start=1):
        final = out_dir / f"frame-{idx:03d}_{hhmmss(ts).replace(':', 'm')}.jpg"
        # -ss before -i seeks by keyframe index instead of decoding from zero,
        # which keeps a per-frame loop cheap even on a long recording.
        proc = run([
            ffmpeg, "-y", "-ss", f"{ts:.3f}", "-i", str(media),
            "-frames:v", "1", "-q:v", "3", str(final),
        ])
        if proc.returncode != 0 or not final.exists():
            continue
        frames.append({
            "file": final.name,
            "path": str(final),
            "timestamp": round(ts, 2),
            "at": hhmmss(ts),
            "bytes": final.stat().st_size,
        })

    if not frames:
        print("WARNING: no frames could be captured from this source.",
              file=sys.stderr)
    return flag_low_information(frames)


def flag_low_information(frames: list[dict]) -> list[dict]:
    """Mark frames that are almost certainly not worth opening.

    A scene-cut detector fires on fades and transition cards as readily as on a
    slide, so a run typically includes several frames that are a title wipe or a
    near-black gradient. Opening one costs well over a thousand tokens and
    returns nothing, and the caller has no way to tell from a timestamp.

    JPEG size is a free proxy for how much is on screen: at fixed quality, a flat
    frame compresses to almost nothing while a dense slide does not. Measured
    over a 3h41m tutorial -- blank transitions landed at 4.5-4.9 KB against a
    36.5 KB median and 54.7 KB for an architecture diagram.

    It is a hint, not a verdict: a dark screenshot with real content can land
    mid-pack, so this flags only the bottom decile and never deletes anything.
    """
    if len(frames) < 10:
        return frames
    sizes = sorted(f["bytes"] for f in frames)
    cutoff = sizes[max(0, len(sizes) // 10)]
    for f in frames:
        f["likely_blank"] = f["bytes"] <= cutoff
    return frames


# --------------------------------------------------------------------------


def write_transcript_md(path: Path, meta: dict, segments: list[dict]) -> None:
    lines = [f"# Transcript: {meta['title']}", ""]
    if meta.get("url"):
        lines += [f"Source: {meta['url']}", ""]
    for seg in merge_segments(segments):
        lines.append(f"**[{hhmmss(seg['start'])}]** {seg['text']}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source", help="URL or path to a local video/audio file")
    ap.add_argument("--work-dir", help="Where to stage output (default: temp dir per source)")
    ap.add_argument("--scene-threshold", type=float, default=0.30,
                    help="ffmpeg scene-change sensitivity, 0-1. Lower = more frames.")
    ap.add_argument("--max-frames", type=int, default=40)
    ap.add_argument("--whisper-model", default="base",
                    help="faster-whisper size: tiny, base, small, medium")
    ap.add_argument("--no-frames", action="store_true", help="Transcript only")
    ap.add_argument("--force-asr", action="store_true",
                    help="Transcribe locally even if captions exist. Worth it when "
                         "a platform's auto-captions are poor.")
    ap.add_argument("--keep-media", action="store_true",
                    help="Keep the downloaded video after frame extraction")
    args = ap.parse_args()

    ffmpeg = find_tool("ffmpeg")
    ffprobe = find_tool("ffprobe")
    ytdlp = find_tool("yt-dlp")

    src_path = Path(args.source)
    is_local = src_path.exists()

    # A mistyped or relative file path would otherwise fall through to the URL
    # branch and fail as "not a valid URL", which points at the wrong problem.
    if not is_local and not re.match(r"https?://", args.source, re.I):
        looks_like_path = (
            src_path.suffix.lower() in VIDEO_SUFFIXES | AUDIO_SUFFIXES
            or any(sep in args.source for sep in ("/", "\\"))
        )
        if looks_like_path:
            die(f"No such file: {src_path}\n"
                f"Resolved against the current directory: {Path.cwd()}\n"
                f"Pass an absolute path, or cd to the file's folder first.")

    if not is_local and not ytdlp:
        die("yt-dlp is not installed and the source is not a local file.\n"
            f"Install it with:  {INSTALL_HINTS['yt-dlp']}")
    if not args.no_frames and not ffmpeg:
        die("ffmpeg is not installed, so frames cannot be extracted.\n"
            f"Install it with:  {INSTALL_HINTS['ffmpeg']}\n"
            "Or re-run with --no-frames for a transcript-only pass.")

    work = Path(args.work_dir) if args.work_dir else Path(
        tempfile.mkdtemp(prefix="videoingest-")
    )
    work.mkdir(parents=True, exist_ok=True)

    # --- acquire ---------------------------------------------------------
    if is_local:
        print(f"  local source: {src_path.name}", file=sys.stderr)
        meta = probe_local(src_path, ffprobe)
        media = src_path
        sub_path = find_sibling_subtitles(src_path)
        if sub_path:
            print(f"  found sibling captions: {sub_path.name}", file=sys.stderr)
    else:
        print("  resolving with yt-dlp...", file=sys.stderr)
        want_video = not args.no_frames
        meta, media, sub_path = fetch_remote(
            args.source, work, ytdlp, want_video, ffmpeg, args.force_asr
        )

    # --- transcript ------------------------------------------------------
    if args.force_asr and media:
        sub_path = None
    if sub_path:
        segments = parse_subtitle_file(sub_path)
        meta["transcript_source"] = f"subtitles ({sub_path.suffix.lstrip('.')})"
    elif media:
        if not ffmpeg:
            die("No subtitles available and ffmpeg is missing, so audio cannot "
                "be extracted for transcription.")
        segments = transcribe(media, ffmpeg, args.whisper_model)
        meta["transcript_source"] = f"faster-whisper ({args.whisper_model})"
    else:
        die("Could not obtain a transcript or any media to transcribe.")

    if not segments:
        die("Transcript came back empty. The source may have no speech, or the "
            "caption file may be malformed.")

    if not meta.get("duration") and segments:
        meta["duration"] = segments[-1]["end"]

    (work / "transcript.json").write_text(
        json.dumps(segments, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    write_transcript_md(work / "transcript.md", meta, segments)

    # --- frames ----------------------------------------------------------
    frames: list[dict] = []
    if not args.no_frames and media and ffmpeg:
        if media.suffix.lower() in AUDIO_SUFFIXES:
            print("  audio-only source; skipping frame extraction", file=sys.stderr)
        else:
            print("  extracting scene-change frames...", file=sys.stderr)
            frames = extract_frames(
                media, work / "frames", ffmpeg, args.scene_threshold,
                args.max_frames, meta.get("duration"),
            )

    (work / "frames.json").write_text(
        json.dumps(frames, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    meta["slug"] = slugify(meta["title"])
    meta["word_count"] = sum(len(s["text"].split()) for s in segments)
    (work / "meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # Downloaded media is large and fully derivable from the URL; the frames and
    # transcript are what matter downstream. Never touch a user's local file.
    if not args.keep_media and not is_local:
        for leftover in work.glob("media.*"):
            leftover.unlink(missing_ok=True)

    summary = {
        "work_dir": str(work),
        "title": meta["title"],
        "slug": meta["slug"],
        "url": meta.get("url"),
        "duration": meta.get("duration"),
        "duration_human": hhmmss(meta["duration"]) if meta.get("duration") else None,
        "transcript_source": meta["transcript_source"],
        "word_count": meta["word_count"],
        "segments": len(segments),
        "frame_count": len(frames),
        "transcript_md": str(work / "transcript.md"),
        "frames_json": str(work / "frames.json"),
    }
    # ensure_ascii escapes non-ASCII to \uXXXX rather than dropping it, so the
    # summary survives any console encoding losslessly and still parses back to
    # the real title. The on-disk meta.json keeps the characters verbatim.
    print(json.dumps(summary, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
