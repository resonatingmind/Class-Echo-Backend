"""Re-score stored transcripts several times and report how much the scores drift.

Phase 0 check before teachers start tracking progress: if the same transcript
scores more than about 5 points apart across runs, trends are noise.

Text-only: it uses each recording's stored transcript, so no audio is
downloaded, but every run is one real Gemini scoring call (N x RUNS calls in
total). Nothing is written to the database.

    docker compose exec backend python scripts/scoring_consistency.py --recordings 10 --runs 3
    docker compose exec backend python scripts/scoring_consistency.py --ids 12 15 --runs 3 --json out.json
"""

import argparse
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from google.genai import errors as genai_errors  # noqa: E402
from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

import app.models  # noqa: E402,F401  (registers every mapper)
from app.config import settings  # noqa: E402
from app.models.recording import Recording, RecordingStatus  # noqa: E402
from app.services.llm_pipeline.config import GEMINI_MODEL, RUBRIC_VERSION, SCORE_WEIGHTS  # noqa: E402
from app.services.llm_pipeline.evaluator import evaluate  # noqa: E402
from app.tasks.llm_tasks import build_reference_context  # noqa: E402

DRIFT_LIMIT = 5


def _spread(values: list[float]) -> float:
    return max(values) - min(values) if values else 0.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--recordings", type=int, default=10, help="how many recent completed recordings")
    parser.add_argument("--ids", type=int, nargs="*", help="specific recording ids instead")
    parser.add_argument("--runs", type=int, default=3, help="scoring runs per recording")
    parser.add_argument("--json", help="also write the full results to this file")
    args = parser.parse_args()

    engine = create_engine(settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://"))
    Session = sessionmaker(bind=engine)

    with Session() as session:
        query = select(Recording).where(
            Recording.status == RecordingStatus.completed,
            Recording.transcription.is_not(None),
        )
        if args.ids:
            query = query.where(Recording.id.in_(args.ids))
        else:
            query = query.order_by(Recording.processed_at.desc()).limit(args.recordings)
        recordings = list(session.scalars(query))
        if not recordings:
            print("No completed recordings with a stored transcript.")
            return 1

        print(f"Model {GEMINI_MODEL} · rubric {RUBRIC_VERSION} · {len(recordings)} recordings × {args.runs} runs\n")
        results = []
        for rec in recordings:
            context = build_reference_context(session, rec)
            runs = []
            for i in range(args.runs):
                # evaluate() mutates the context it is given; each run gets a fresh copy.
                try:
                    result = evaluate(None, reference_context=dict(context))
                except genai_errors.APIError as exc:
                    # A busy model (503) after the evaluator's own retries should
                    # cost one run, not the results gathered so far.
                    print(f"  #{rec.id} run {i + 1}: skipped ({exc.code} {exc.status})")
                    continue
                runs.append({
                    "total": result["total"],
                    "scores": {d: result["scores"][d]["score"] for d in SCORE_WEIGHTS},
                    "focus_area": (result["improvements"][0]["area"] if result["improvements"] else None),
                })
                print(f"  #{rec.id} run {i + 1}: {result['total']}")
            if len(runs) < 2:
                print(f"#{rec.id:<5} fewer than 2 successful runs, left out of the summary\n")
                continue
            totals = [r["total"] for r in runs]
            dims = {d: _spread([r["scores"][d] for r in runs]) for d in SCORE_WEIGHTS}
            focus_areas = {r["focus_area"] for r in runs}
            row = {
                "recording_id": rec.id,
                "title": rec.lecture_title,
                "totals": totals,
                "spread": _spread(totals),
                "stdev": round(statistics.pstdev(totals), 2),
                "dimension_spread": dims,
                "focus_stable": len(focus_areas) == 1,
                "runs": runs,
            }
            results.append(row)
            print(
                f"#{rec.id:<5} totals {totals}  spread {row['spread']:g}"
                f"  focus {'stable' if row['focus_stable'] else 'CHANGES: ' + ', '.join(map(str, focus_areas))}\n"
            )

    if not results:
        print("No recording had 2 successful runs; nothing to compare.")
        return 1
    spreads = [r["spread"] for r in results]
    over = [r for r in results if r["spread"] > DRIFT_LIMIT]
    print("── Summary ──")
    print(f"Mean total spread: {statistics.mean(spreads):.1f} · worst: {max(spreads):g}")
    print("Worst dimensions (mean spread):")
    for dim in sorted(SCORE_WEIGHTS, key=lambda d: -statistics.mean(r["dimension_spread"][d] for r in results)):
        mean = statistics.mean(r["dimension_spread"][dim] for r in results)
        print(f"  {dim:<24} {mean:.1f} / {SCORE_WEIGHTS[dim]}")
    print(f"Focus area stable in {sum(r['focus_stable'] for r in results)}/{len(results)} recordings")
    verdict = "FAIL" if over else "PASS"
    print(f"\n{verdict}: {len(over)} of {len(results)} recordings drift more than {DRIFT_LIMIT} points")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(
                {"model": GEMINI_MODEL, "rubric_version": RUBRIC_VERSION, "runs": args.runs, "results": results},
                f, indent=2, ensure_ascii=False,
            )
    return 1 if over else 0


if __name__ == "__main__":
    sys.exit(main())
