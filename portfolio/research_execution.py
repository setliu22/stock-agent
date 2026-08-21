"""Compile research plans into validated, executable requests."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Callable

from .company_resolver import (
    InstrumentResolutionError,
    ResolvedInstrument,
    extract_security_reference,
    resolve_instrument,
)
from .research_plan import ResearchPlan


InstrumentResolver = Callable[[str], ResolvedInstrument]
_NAMED_COMPANY_WORKFLOWS = {
    "company_deep_dive",
    "company_compare",
    "position_review",
    "research_lab",
}


@dataclass(frozen=True)
class ValidatedResearchRequest:
    """An execution request whose named securities have already been resolved."""

    plan: ResearchPlan
    resolved: tuple[ResolvedInstrument, ...] = ()

    @property
    def rics(self) -> tuple[str, ...]:
        return tuple(item.ric for item in self.resolved)


def compile_execution_request(
    plan: ResearchPlan,
    resolver: InstrumentResolver = resolve_instrument,
) -> ValidatedResearchRequest:
    """Validate the plan and resolve all entities before evidence collection.

    Resolution is atomic: callers receive either a complete immutable request or
    an exception. No partially resolved company list can reach an LSEG workflow.
    """

    normalized = deepcopy(plan).normalized()
    if normalized.workflow not in _NAMED_COMPANY_WORKFLOWS:
        return ValidatedResearchRequest(plan=normalized)

    references = [extract_security_reference(entity) for entity in normalized.entities]
    resolved: list[ResolvedInstrument] = []
    seen_rics: dict[str, str] = {}
    for reference in references:
        instrument = resolver(reference)
        if not isinstance(instrument, ResolvedInstrument):
            raise InstrumentResolutionError(
                f"The resolver returned an invalid result for {reference!r}."
            )
        if not instrument.ticker.strip() or not instrument.ric.strip():
            raise InstrumentResolutionError(
                f"The resolver returned an incomplete instrument for {reference!r}."
            )
        ric_key = instrument.ric.strip().casefold()
        if ric_key in seen_rics:
            raise InstrumentResolutionError(
                f"{seen_rics[ric_key]!r} and {reference!r} resolve to the same listed security "
                f"({instrument.ric}). Remove the duplicate reference."
            )
        seen_rics[ric_key] = reference
        resolved.append(instrument)

    return ValidatedResearchRequest(plan=normalized, resolved=tuple(resolved))
