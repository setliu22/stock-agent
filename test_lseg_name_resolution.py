from __future__ import annotations

import json
import sys
import traceback

from portfolio.config import get_settings
from portfolio.lseg_research import answer_follow_up, concise_report, run_research
from portfolio.research_planner import build_research_plan


def main() -> int:
    query = " ".join(sys.argv[1:]).strip() or "Analyze Palantir"
    settings = get_settings()
    try:
        print(f"Natural-language request: {query!r}")
        plan = build_research_plan(query, settings)
        print(f"Workflow: {plan.workflow}")
        print(f"Intent: {plan.to_dict()}")
        if plan.mode == "screen" and plan.screen.candidate_search:
            print("Correctly interpreted as the fixed sector_opportunity workflow; no company was invented and the LLM did not generate API calls.")
        result = run_research(plan, settings)
        print("LSEG session and deterministic workflow succeeded.")
        if result.metrics.get("screen_expression"):
            print(f"Screen expression: {result.metrics['screen_expression']}")
        print("Calls:")
        for record in result.call_records:
            print("  - " + json.dumps(record, default=str, sort_keys=True))
        print("\nConcise report:\n")
        print(concise_report(result, settings))
        for follow_up in (
            "why is this company undervalued?",
            "what are the major risks?",
            "what's the catalyst?",
        ):
            print(f"\nFollow-up: {follow_up}\n")
            print(answer_follow_up(result, follow_up, settings))
        return 0
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
