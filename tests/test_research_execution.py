from __future__ import annotations

import pytest

from portfolio.company_resolver import InstrumentResolutionError, ResolvedInstrument
from portfolio.research_execution import compile_execution_request
from portfolio.research_plan import ResearchPlan, ScreenFilters


def _resolved(reference: str, ticker: str, ric: str) -> ResolvedInstrument:
    return ResolvedInstrument(reference, reference, ticker, ric, f"{ticker} Company")


def test_compiler_normalizes_and_resolves_before_execution() -> None:
    calls: list[str] = []

    def resolver(reference: str) -> ResolvedInstrument:
        calls.append(reference)
        return _resolved(reference, "ZBRA", "ZBRA.O")

    request = compile_execution_request(
        ResearchPlan(mode="company", entities=["do some research on zbra"]),
        resolver=resolver,
    )

    assert calls == ["zbra"]
    assert request.rics == ("ZBRA.O",)
    assert request.plan.workflow == "company_deep_dive"


def test_compiler_fails_atomically_when_any_company_cannot_resolve() -> None:
    calls: list[str] = []

    def resolver(reference: str) -> ResolvedInstrument:
        calls.append(reference)
        if reference == "UnknownCo":
            raise InstrumentResolutionError("No listed security matched 'UnknownCo'.")
        return _resolved(reference, "AAPL", "AAPL.O")

    with pytest.raises(InstrumentResolutionError, match="UnknownCo"):
        compile_execution_request(
            ResearchPlan(mode="compare", entities=["Apple", "UnknownCo"]),
            resolver=resolver,
        )

    assert calls == ["Apple", "UnknownCo"]


def test_compiler_rejects_duplicate_resolved_security() -> None:
    with pytest.raises(InstrumentResolutionError, match="same listed security"):
        compile_execution_request(
            ResearchPlan(mode="compare", entities=["Apple", "AAPL"]),
            resolver=lambda reference: _resolved(reference, "AAPL", "AAPL.O"),
        )


def test_screen_compilation_never_invokes_company_resolver() -> None:
    request = compile_execution_request(
        ResearchPlan(mode="screen", screen=ScreenFilters(sector="Technology")),
        resolver=lambda _reference: (_ for _ in ()).throw(AssertionError("must not resolve")),
    )

    assert request.resolved == ()
    assert request.plan.workflow == "stock_screen"
