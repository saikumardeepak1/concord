"""Run the eval suite. Used by `concord evals` and by CI."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any

from concord.orchestrator import Concord
from evals.harness import load_cases, run_case


async def run_suite(category: str = "all", parallelism: int = 4) -> dict[str, Any]:
    cases = load_cases(category)
    if not cases:
        return {"total": 0, "passed": 0, "per_category": {}, "details": []}

    concord = Concord()
    sem = asyncio.Semaphore(parallelism)

    async def _one(c):
        async with sem:
            return await run_case(concord, c)

    results = await asyncio.gather(*[_one(c) for c in cases])
    by_cat: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "passed": 0})
    for r in results:
        by_cat[r.category]["total"] += 1
        if r.passed:
            by_cat[r.category]["passed"] += 1

    return {
        "total": len(results),
        "passed": sum(1 for r in results if r.passed),
        "per_category": dict(by_cat),
        "details": [
            {
                "id": r.case_id,
                "category": r.category,
                "passed": r.passed,
                "failures": r.failures,
                "outcome": r.outcome,
                "confidence": r.confidence,
                "duration_ms": r.duration_ms,
                "response": r.response_text[:300],
            }
            for r in results
        ],
    }


if __name__ == "__main__":
    out = asyncio.run(run_suite())
    print(f"pass: {out['passed']}/{out['total']}")
    for cat, stats in out["per_category"].items():
        print(f"  {cat}: {stats['passed']}/{stats['total']}")
