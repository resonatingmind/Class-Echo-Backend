"""Check that transcript segment times match the audio, early and late in a lecture.

Phase 5 check before clips are trusted: Gemini returns a start time for every
segment, and on a long lecture those times can drift, so a clip would play the
wrong moment. For each recording this script picks a few segments spread over
the lecture, cuts a 30 s window of audio around each claimed start, transcribes
just that window (short audio times well), finds the same words in it and
measures how far the claimed start is from where they really are.

If drift grows past DRIFT_LIMIT seconds, long lectures need to be transcribed
in 10-minute chunks with the times offset.

Gemini calls: one short call per probe, plus one full transcription for each
recording without stored segments (or all of them with --transcribe). Nothing
is written to the database.

    docker compose exec backend python scripts/timestamp_check.py --recordings 5 --probes 6
    docker compose exec backend python scripts/timestamp_check.py --ids 12 15 --transcribe --json out.json
"""

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import tempfile
from difflib import SequenceMatcher

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

import app.models  # noqa: E402,F401  (registers every mapper)
from app.config import settings  # noqa: E402
from app.models.recording import Recording, RecordingStatus  # noqa: E402
from app.services.llm import _download_audio  # noqa: E402
from app.services.llm_pipeline.evaluator import transcribe_audio_segments  # noqa: E402
from app.services.llm_pipeline.segments import format_clock  # noqa: E402
from app.services.s3_service import sign_url  # noqa: E402

DRIFT_LIMIT = 5
WINDOW_LEAD = 10
WINDOW_SECONDS = 30
# Below this the window's words only loosely resemble the segment (a repeated
# phrase, say), and the offset would measure the wrong line.
MIN_MATCH = 0.65


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


def _audio_path(recording: Recording) -> tuple[str, bool]:
    """A local file for the recording's audio, and whether it is a temp file to delete."""
    if os.path.exists(recording.cloudinary_url):
        return recording.cloudinary_url, False
    return _download_audio(sign_url(recording.cloudinary_url) or recording.cloudinary_url), True


def _duration(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def _cut(path: str, start: float, seconds: float) -> str:
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        out = tmp.name
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.2f}", "-t", f"{seconds:.2f}", "-i", path,
         "-ac", "1", "-ar", "16000", out],
        check=True,
    )
    return out


def _pick_probes(segments: list[dict], count: int) -> list[dict]:
    """Segments spread evenly over the lecture, long enough to match reliably."""
    usable = [s for s in segments if len(_words(s["text"])) >= 5]
    if len(usable) <= count:
        return usable
    step = (len(usable) - 1) / (count - 1) if count > 1 else 0
    return [usable[round(i * step)] for i in range(count)]


def _best_match(target: str, candidates: list[dict]) -> tuple[dict | None, float]:
    """The window segment saying the same words, also trying adjacent pairs (splits differ)."""
    want = _words(target)
    best, best_ratio = None, 0.0
    for i, seg in enumerate(candidates):
        options = [_words(seg["text"])]
        if i + 1 < len(candidates):
            options.append(_words(seg["text"] + " " + candidates[i + 1]["text"]))
        for words in options:
            ratio = SequenceMatcher(None, want, words).ratio()
            if ratio > best_ratio:
                best, best_ratio = seg, ratio
    return best, best_ratio


def check_recording(recording: Recording, probes: int, transcribe: bool) -> dict:
    path, temp = _audio_path(recording)
    try:
        duration = _duration(path)
        segments = None if transcribe else recording.transcript_segments
        source = "stored"
        if not segments:
            _, segments = transcribe_audio_segments(path, duration)
            source = "fresh"
        if not segments:
            return {"id": recording.id, "duration": duration, "error": "no segments (plain-transcript fallback)"}

        results = []
        for seg in _pick_probes(segments, probes):
            window_start = max(0.0, seg["start"] - WINDOW_LEAD)
            clip = _cut(path, window_start, WINDOW_SECONDS)
            try:
                _, local = transcribe_audio_segments(clip, WINDOW_SECONDS)
            except Exception as exc:  # one failed probe should not end the run
                results.append({"claimed": seg["start"], "error": str(exc)[:120]})
                continue
            finally:
                os.unlink(clip)
            match, ratio = _best_match(seg["text"], local or [])
            row = {"claimed": seg["start"], "text": seg["text"][:60], "match": round(ratio, 2)}
            if match is not None and ratio >= MIN_MATCH:
                row["actual"] = round(window_start + match["start"], 1)
                row["offset"] = round(row["actual"] - seg["start"], 1)
            results.append(row)

        offsets = [abs(r["offset"]) for r in results if "offset" in r]
        return {
            "id": recording.id,
            "duration": duration,
            "segments": source,
            "last_segment_end": max(s["end"] for s in segments),
            "probes": results,
            "median_abs_offset": round(statistics.median(offsets), 1) if offsets else None,
            "max_abs_offset": max(offsets) if offsets else None,
            "unmatched": sum(1 for r in results if "offset" not in r),
        }
    finally:
        if temp:
            os.unlink(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--recordings", type=int, default=5, help="how many completed recordings, longest first")
    parser.add_argument("--ids", type=int, nargs="*", help="specific recording ids instead")
    parser.add_argument("--probes", type=int, default=6, help="moments checked per recording")
    parser.add_argument("--transcribe", action="store_true", help="transcribe again even when segments are stored")
    parser.add_argument("--json", help="also write the full results to this file")
    args = parser.parse_args()

    engine = create_engine(settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://"))
    Session = sessionmaker(bind=engine)
    with Session() as session:
        query = select(Recording).where(Recording.status == RecordingStatus.completed)
        if args.ids:
            query = query.where(Recording.id.in_(args.ids))
        else:
            # Longest first: drift shows up late in long lectures.
            query = query.order_by(Recording.duration_seconds.desc().nulls_last()).limit(args.recordings)
        recordings = list(session.scalars(query))

    reports = []
    for recording in recordings:
        print(f"\nRecording {recording.id} …", flush=True)
        try:
            report = check_recording(recording, args.probes, args.transcribe)
        except Exception as exc:
            report = {"id": recording.id, "error": str(exc)[:200]}
        reports.append(report)
        if "error" in report:
            print(f"  error: {report['error']}")
            continue
        print(f"  {format_clock(report['duration'])} long, {report['segments']} segments, "
              f"last segment ends {format_clock(report['last_segment_end'])}")
        for p in report["probes"]:
            if "offset" in p:
                print(f"  {format_clock(p['claimed'])} → {format_clock(p['actual'])}  "
                      f"offset {p['offset']:+.1f}s  (match {p['match']})")
            else:
                reason = p.get("error") or f"best match {p.get('match')}"
                print(f"  {format_clock(p['claimed'])} → not found ({reason})")
        print(f"  median |offset| {report['median_abs_offset']}s, max {report['max_abs_offset']}s, "
              f"unmatched {report['unmatched']}")

    measured = [r for r in reports if r.get("max_abs_offset") is not None]
    drifting = [r["id"] for r in measured if r["max_abs_offset"] > DRIFT_LIMIT]
    print("\n" + "=" * 60)
    print(f"{len(measured)} of {len(reports)} recordings measured; drift limit {DRIFT_LIMIT}s")
    print(f"Drifting: {drifting or 'none'}")
    if args.json:
        with open(args.json, "w") as f:
            json.dump(reports, f, indent=2)
    return 1 if drifting else 0


if __name__ == "__main__":
    sys.exit(main())
