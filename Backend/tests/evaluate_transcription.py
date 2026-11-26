"""Measure speech-recognition quality on real lecture recordings.

Run from Backend:  python tests/evaluate_transcription.py RECORDING [RECORDING ...] [--model openai/whisper-base] [--report out.json]

Each recording needs a human-checked reference transcript beside it with the same name and a
.txt extension (for example week3.mp3 and week3.txt). The report gives word error rate (WER),
its parts, and the real-time factor on this machine. This scores transcription only; note
quality and answer quality are evaluated separately. The first run downloads the Whisper model.
"""
import argparse
import json
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.services import media_service, transcript_service  # noqa: E402
from app.services.transcription_quality import word_errors  # noqa: E402


def evaluate(recordings: list[Path]) -> dict:
    results, totals = [], {"reference_words": 0, "errors": 0, "audio_seconds": 0.0, "processing_seconds": 0.0}
    for recording in recordings:
        reference = recording.with_suffix(".txt")
        if not reference.is_file():
            raise SystemExit(f"Missing reference transcript {reference.name} for {recording.name}")
        info = media_service.probe(str(recording))
        started = time.perf_counter()
        segments = transcript_service.transcribe(str(recording), info["duration"])
        elapsed = time.perf_counter() - started
        hypothesis = " ".join(segment["text"] for segment in segments)
        score = word_errors(reference.read_text(encoding="utf-8-sig"), hypothesis)
        errors = score["substitutions"] + score["deletions"] + score["insertions"]
        results.append({"recording": recording.name, "duration_seconds": round(info["duration"], 1),
                        "processing_seconds": round(elapsed, 1), "real_time_factor": round(elapsed / info["duration"], 3),
                        "segments": len(segments), **score, "hypothesis": hypothesis})
        totals["reference_words"] += score["reference_words"]
        totals["errors"] += errors
        totals["audio_seconds"] += info["duration"]
        totals["processing_seconds"] += elapsed
        print(f"{recording.name}: WER {score['wer']:.1%} ({errors} errors / {score['reference_words']} words), "
              f"{elapsed:.0f}s for {info['duration']:.0f}s of audio")
    overall = totals["errors"] / totals["reference_words"] if totals["reference_words"] else 0.0
    print(f"Overall WER {overall:.1%} across {len(results)} recording(s); "
          f"real-time factor {totals['processing_seconds'] / max(totals['audio_seconds'], 1e-9):.2f}")
    return {"evaluated_at": datetime.now(timezone.utc).isoformat(), "model": settings.WHISPER_MODEL,
            "language": settings.TRANSCRIPTION_LANGUAGE or "auto", "machine": platform.processor() or platform.machine(),
            "overall_wer": overall, "totals": totals, "recordings": results}


def main():
    parser = argparse.ArgumentParser(description="Score Whisper transcripts against reference transcripts")
    parser.add_argument("recordings", nargs="+", type=Path)
    parser.add_argument("--model", help="Hugging Face Whisper model, e.g. openai/whisper-tiny")
    parser.add_argument("--report", type=Path, help="Write the full JSON report here")
    args = parser.parse_args()
    if args.model:
        settings.WHISPER_MODEL = args.model
    settings.TRANSCRIPTION_PROVIDER = "whisper"
    report = evaluate(args.recordings)
    if args.report:
        args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Report written to {args.report}")


if __name__ == "__main__":
    main()
