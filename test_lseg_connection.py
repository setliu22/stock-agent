from __future__ import annotations

import json
import traceback

from portfolio.config import get_settings
from portfolio.lseg_research import concise_report, run_research
from portfolio.research_plan import ResearchPlan, ScreenFilters


def main() -> int:
    settings = get_settings()
    plan = ResearchPlan(
        mode="screen",
        workflow="sector_opportunity",
        topics=["profile", "valuation"],
        selection_objectives=["relative_value"],
        screen=ScreenFilters(
            sector="Industrials",
            limit=5,
            limit_explicit=True,
            sort_by="quality_value",
            candidate_search=True,
        ),
        raw_request="LSEG connection test: Industrials; 5 results",
    ).normalized()
    try:
        print("Testing the fixed Industrials research workflow.")
        result = run_research(plan, settings)
        print("LSEG session and deterministic workflow succeeded.")
        if result.metrics.get("screen_expression"):
            print(f"Screen expression: {result.metrics['screen_expression']}")
        print("Calls:")
        for record in result.call_records:
            print("  - " + json.dumps(record, default=str, sort_keys=True))
        print("\nConcise report:\n")
        print(concise_report(result, settings))
        return 0
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
