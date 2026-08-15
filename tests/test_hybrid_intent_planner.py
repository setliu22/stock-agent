from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from portfolio.config import Settings
from portfolio.research_planner import (
    LLMIntentDraft,
    ResearchClarificationNeeded,
    UnsupportedResearchConstraint,
    _extract_json,
    _parse_intent_draft,
    build_research_plan,
)


def settings(tmp_path: Path) -> Settings:
    return Settings(
        tmp_path,
        tmp_path / "db.sqlite",
        "configured-for-test",
        "test-model",
        "desktop.workspace",
    )


def intent_draft(**overrides: Any) -> LLMIntentDraft:
    values: dict[str, Any] = {
        "route": "new_research",
        "subject_kind": "stock_universe",
        "entities": (),
        "sector": None,
        "industry": None,
        "country_code": None,
        "market_cap_min": None,
        "market_cap_max": None,
        "pe_max": None,
        "forward_pe_max": None,
        "ev_ebitda_max": None,
        "dividend_yield_min": None,
        "total_return_3m_min": None,
        "limit": None,
        "lookback_days": None,
        "investment_horizon": None,
        "objectives": (),
        "topics": (),
        "confidence": 0.95,
        "clarification": None,
        "interpretation": "Grounded test interpretation.",
        "grounding": {
            "country_code": None,
            "sector": None,
            "industry": None,
            "investment_horizon": None,
            "objectives": {},
            "topics": {},
        },
    }
    values.update(overrides)
    return LLMIntentDraft(**values)


def intent_payload(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "route": "new_research",
        "subject_kind": "stock_universe",
        "entities": [],
        "country": None,
        "country_evidence": None,
        "sector": None,
        "sector_evidence": None,
        "industry": None,
        "industry_evidence": None,
        "investment_horizon": None,
        "investment_horizon_evidence": None,
        "objectives": [],
        "objective_evidence": [],
        "topics": [],
        "topic_evidence": [],
        "confidence": 0.95,
        "clarification": None,
        "interpretation": "Grounded test interpretation.",
    }
    values.update(overrides)
    return values


def test_complete_deterministic_anchors_bypass_llm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "portfolio.research_planner._llm_intent_draft",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("complete deterministic plans must bypass the model")
        ),
    )

    plan = build_research_plan(
        "Looking across the top 7 US industrial stocks with market cap under $5B "
        "and forward P/E below 18, which name stands out long term?",
        settings(tmp_path),
    )

    assert plan.screen.country_code == "US"
    assert plan.screen.sector == "Industrials"
    assert plan.screen.industry is None
    assert plan.screen.market_cap_max == 5_000_000_000
    assert plan.screen.forward_pe_max == 18
    assert plan.screen.limit == 7
    assert plan.investment_horizon == "long_term"
    assert plan.selection_objectives == ["positive_signals"]
    assert plan.workflow == "sector_opportunity"
    assert plan.intent_resolution == {
        "llm_used": False,
        "resolution": "deterministic_complete",
    }


def test_clear_deterministic_request_does_not_call_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "portfolio.research_planner._llm_intent_draft",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("complete deterministic plans must bypass the model")
        ),
    )

    plan = build_research_plan(
        "find US technology stocks with P/E below 20",
        settings(tmp_path),
    )

    assert plan.planner == "deterministic"
    assert plan.screen.country_code == "US"
    assert plan.screen.sector == "Technology"
    assert plan.screen.pe_max == 20
    assert plan.intent_resolution["llm_used"] is False


def test_listing_country_policy_rejects_before_llm_interpretation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def should_not_run(*_args: Any, **_kwargs: Any) -> LLMIntentDraft:
        raise AssertionError("hard policy failures must be decided before the LLM")

    monkeypatch.setattr("portfolio.research_planner._llm_intent_draft", should_not_run)

    with pytest.raises(UnsupportedResearchConstraint, match="listing|Exchange"):
        build_research_plan("US-listed Chinese biotech stocks", settings(tmp_path))


@pytest.mark.parametrize("follow_up", ["focus on names headquartered stateside", "study US stocks"])
def test_stateside_follow_up_retains_prior_biotech_classification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    follow_up: str,
) -> None:
    prior = build_research_plan(
        "study biotech stocks",
        Settings(tmp_path, tmp_path / "db.sqlite", None, "test-model", "desktop.workspace"),
    )
    monkeypatch.setattr(
        "portfolio.research_planner._llm_intent_draft",
        lambda *_args, **_kwargs: intent_draft(
            route="refine_screen",
            country_code="US",
            interpretation="Restrict the prior biotech universe to US-headquartered names.",
            grounding={
                "country_code": "stateside" if "stateside" in follow_up else "US",
                "sector": None,
                "industry": None,
                "investment_horizon": None,
                "objectives": {},
                "topics": {},
            },
        ),
    )

    plan = build_research_plan(follow_up, settings(tmp_path), prior_plan=prior)

    assert plan.planner == (
        "hybrid_llm_validated" if "stateside" in follow_up else "deterministic_contextual"
    )
    assert plan.screen.country_code == "US"
    assert plan.screen.sector == "Healthcare"
    assert plan.screen.industry == "Biotechnology & Medical Research"
    assert plan.context_parent_request == "study biotech stocks"


def test_grounded_stateside_interpretation_keeps_deterministic_numeric_anchor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "portfolio.research_planner._llm_intent_draft",
        lambda *_args, **_kwargs: intent_draft(
            country_code="US",
            grounding={
                "country_code": "stateside",
                "sector": None,
                "industry": None,
                "investment_horizon": None,
                "objectives": {},
                "topics": {},
            },
            interpretation="Use US headquarters for stateside.",
        ),
    )

    plan = build_research_plan(
        "find stateside industrial stocks with market cap under $5B",
        settings(tmp_path),
    )

    assert plan.screen.country_code == "US"
    assert plan.screen.sector == "Industrials"
    assert plan.screen.market_cap_max == 5_000_000_000


def test_lowercase_us_pronoun_cannot_become_a_country_filter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "portfolio.research_planner._llm_intent_draft",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("complete deterministic plans must bypass the model")
        ),
    )

    plan = build_research_plan("show us biotech stocks", settings(tmp_path))

    assert plan.screen.country_code is None
    assert plan.screen.industry == "Biotechnology & Medical Research"
    assert plan.intent_resolution["llm_used"] is False


def test_unresolved_stateside_wording_never_falls_back_to_broader_screen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "portfolio.research_planner._llm_intent_draft",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("provider outage")),
    )

    with pytest.raises(ResearchClarificationNeeded, match="resolve the wording safely"):
        build_research_plan(
            "find stocks headquartered stateside with market cap under $5B",
            settings(tmp_path),
        )


def test_stateside_overrides_inherited_country_not_current_turn_anchor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    no_model = Settings(tmp_path, tmp_path / "db.sqlite", None, "test-model", "desktop.workspace")
    prior = build_research_plan("study Canadian biotech stocks", no_model)
    monkeypatch.setattr(
        "portfolio.research_planner._llm_intent_draft",
        lambda *_args, **_kwargs: intent_draft(
            route="refine_screen",
            country_code="US",
            grounding={
                "country_code": "stateside",
                "sector": None,
                "industry": None,
                "investment_horizon": None,
                "objectives": {},
                "topics": {},
            },
            interpretation="Replace inherited Canada with US headquarters.",
        ),
    )

    plan = build_research_plan("focus on stateside stocks", settings(tmp_path), prior_plan=prior)

    assert plan.screen.country_code == "US"
    assert plan.screen.industry == "Biotechnology & Medical Research"


def test_domestic_does_not_silently_keep_an_inherited_country(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    no_model = Settings(tmp_path, tmp_path / "db.sqlite", None, "test-model", "desktop.workspace")
    prior = build_research_plan("study Canadian biotech stocks", no_model)
    monkeypatch.setattr(
        "portfolio.research_planner._llm_intent_draft",
        lambda *_args, **_kwargs: intent_draft(
            route="needs_clarification",
            subject_kind="stock_universe",
            confidence=0.6,
            clarification="Which country should domestic refer to?",
            interpretation="Domestic has no configured home-country reference.",
        ),
    )

    with pytest.raises(ResearchClarificationNeeded, match="domestic|country"):
        build_research_plan("focus on domestic stocks", settings(tmp_path), prior_plan=prior)


def test_numeric_anchor_is_compiled_even_when_llm_resolves_universe_wording(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "portfolio.research_planner._llm_intent_draft",
        lambda *_args, **_kwargs: intent_draft(
            country_code="US",
            sector="Industrials",
            grounding={
                "country_code": "US",
                "sector": "industrial",
                "industry": None,
                "investment_horizon": None,
                "objectives": {},
                "topics": {},
            },
            interpretation="Screen US industrial names.",
        ),
    )

    plan = build_research_plan(
        "take a closer look at US industrial names with P/E below 10",
        settings(tmp_path),
    )

    assert plan.workflow == "stock_screen"
    assert plan.screen.country_code == "US"
    assert plan.screen.sector == "Industrials"
    assert plan.screen.pe_max == 10


def test_model_cannot_replace_explicit_deterministic_company(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "portfolio.research_planner._llm_intent_draft",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("explicit company requests must bypass the model")
        ),
    )

    plan = build_research_plan("analyze Apple", settings(tmp_path))

    assert plan.entities == ["Apple"]
    assert plan.workflow == "company_deep_dive"
    assert plan.planner == "deterministic"
    assert plan.intent_resolution["llm_used"] is False


def test_invented_entity_in_ambiguous_request_requires_clarification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "portfolio.research_planner._llm_intent_draft",
        lambda *_args, **_kwargs: intent_draft(
            subject_kind="company",
            entities=("Apple",),
            interpretation="Research Apple.",
        ),
    )

    with pytest.raises(ResearchClarificationNeeded, match="clarif|explicit|company|universe"):
        build_research_plan("take a closer look at the standout", settings(tmp_path))


def test_strict_json_rejects_duplicate_extra_and_nonfinite_fields() -> None:
    with pytest.raises(ValueError, match="duplicate JSON key"):
        _extract_json('{"route":"new_research","route":"general"}')

    with pytest.raises(ValueError, match="non-finite JSON value"):
        _extract_json('{"confidence":NaN}')

    payload = intent_payload(lseg_field="TR.PriceClose")
    with pytest.raises(ValueError, match="unknown keys: lseg_field"):
        _parse_intent_draft(payload)

    parsed = _parse_intent_draft(
        intent_payload(
            country="US",
            country_evidence="stateside",
            sector="Industrials",
            sector_evidence="industrial",
            objectives=["relative_value"],
            objective_evidence=["underappreciated"],
        )
    )
    assert parsed.country_code == "US"
    assert parsed.sector == "Industrials"
    assert parsed.objectives == ("relative_value",)
    assert parsed.market_cap_max is None


def test_taxonomy_value_in_wrong_model_slot_is_canonicalized_locally() -> None:
    parsed = _parse_intent_draft(
        intent_payload(
            industry="Industrials",
            industry_evidence="industrial name",
            objectives=["relative_value"],
            objective_evidence=["underappreciated"],
        )
    )

    assert parsed.sector == "Industrials"
    assert parsed.industry is None
    assert parsed.grounding["sector"] == "industrial name"


def test_explicit_list_only_request_cannot_be_promoted_to_candidate_search(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "portfolio.research_planner._llm_intent_draft",
        lambda *_args, **_kwargs: intent_draft(
            objectives=("positive_signals", "relative_value"),
            interpretation="List US industrial stocks and rank attractive value candidates.",
            grounding={
                "country_code": None,
                "sector": None,
                "industry": None,
                "investment_horizon": None,
                "objectives": {
                    "positive_signals": "list",
                    "relative_value": "list",
                },
                "topics": {},
            },
        ),
    )

    plan = build_research_plan("list US industrial stocks", settings(tmp_path))

    assert plan.workflow == "stock_screen"
    assert plan.screen.candidate_search is False
    assert plan.selection_objectives == []


def test_grounded_underappreciated_wording_becomes_relative_value_objective(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "portfolio.research_planner._llm_intent_draft",
        lambda *_args, **_kwargs: intent_draft(
            objectives=("relative_value",),
            grounding={
                "country_code": None,
                "sector": None,
                "industry": None,
                "investment_horizon": None,
                "objectives": {"relative_value": "underappreciated"},
                "topics": {},
            },
            interpretation="Select a relatively undervalued industrial candidate.",
        ),
    )

    plan = build_research_plan(
        "find an underappreciated industrial stock",
        settings(tmp_path),
    )

    assert plan.selection_objectives == ["relative_value"]
    assert plan.workflow == "sector_opportunity"
    assert plan.screen.sector == "Industrials"


def test_candidate_objective_without_peer_taxonomy_requires_clarification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "portfolio.research_planner._llm_intent_draft",
        lambda *_args, **_kwargs: intent_draft(
            country_code="US",
            objectives=("relative_value",),
            grounding={
                "country_code": "US",
                "sector": None,
                "industry": None,
                "investment_horizon": None,
                "objectives": {"relative_value": "undervalued"},
                "topics": {},
            },
            interpretation="Find an undervalued US stock without a peer taxonomy.",
        ),
    )

    with pytest.raises(ResearchClarificationNeeded, match="sector|universe|explicit|validate"):
        build_research_plan("find an undervalued US stock", settings(tmp_path))


def test_wrong_taxonomy_mapping_is_rejected_instead_of_searched(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "portfolio.research_planner._llm_intent_draft",
        lambda *_args, **_kwargs: intent_draft(
            sector="Technology",
            objectives=("positive_signals",),
            grounding={
                "country_code": None,
                "sector": "industrial",
                "industry": None,
                "investment_horizon": None,
                "objectives": {"positive_signals": "compelling"},
                "topics": {},
            },
            interpretation="Incorrectly map industrial to Technology.",
        ),
    )

    with pytest.raises(ResearchClarificationNeeded):
        build_research_plan("hunt for a compelling industrial name", settings(tmp_path))


def test_grounded_pronoun_is_not_accepted_as_a_company_entity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "portfolio.research_planner._llm_intent_draft",
        lambda *_args, **_kwargs: intent_draft(
            subject_kind="company",
            entities=("this stock",),
            interpretation="Treat a pronoun as a company.",
        ),
    )

    with pytest.raises(ResearchClarificationNeeded):
        build_research_plan("take a closer look at this stock", settings(tmp_path))


def test_generated_generic_candidate_cannot_become_company_entity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "portfolio.research_planner._llm_intent_draft",
        lambda *_args, **_kwargs: intent_draft(
            subject_kind="company",
            entities=("most promising one",),
            interpretation="Incorrectly treat a candidate description as a company.",
        ),
    )

    plan = build_research_plan(
        "find most promising one in US biotech industry",
        settings(tmp_path),
    )

    assert plan.mode == "screen"
    assert plan.entities == []
    assert plan.screen.country_code == "US"
    assert plan.screen.industry == "Biotechnology & Medical Research"


def test_generated_general_route_cannot_veto_explicit_screen_anchors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "portfolio.research_planner._llm_intent_draft",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("explicit screen requests must bypass the model")
        ),
    )

    plan = build_research_plan(
        "find top 5 US industrial stocks with P/E below 20",
        settings(tmp_path),
    )

    assert plan.planner == "deterministic"
    assert plan.intent_resolution["llm_used"] is False
    assert plan.screen.country_code == "US"
    assert plan.screen.sector == "Industrials"
    assert plan.screen.pe_max == 20


def test_general_question_remains_general_when_deterministic_entity_is_weak(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from portfolio.research_planner import NotResearchRequest

    monkeypatch.setattr(
        "portfolio.research_planner._llm_intent_draft",
        lambda *_args, **_kwargs: intent_draft(
            route="general",
            subject_kind="none",
            interpretation="A conceptual question, not an equity request.",
        ),
    )

    with pytest.raises(NotResearchRequest):
        build_research_plan("research what diversification means", settings(tmp_path))


def test_ambiguous_request_with_model_failure_requires_clarification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*_args: Any, **_kwargs: Any) -> LLMIntentDraft:
        raise RuntimeError("provider outage")

    monkeypatch.setattr("portfolio.research_planner._llm_intent_draft", fail)

    with pytest.raises(ResearchClarificationNeeded, match="resolve the wording safely"):
        build_research_plan("take a closer look at whichever one fits best", settings(tmp_path))


def test_ambiguous_objective_omission_cannot_broaden_to_plain_screen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "portfolio.research_planner._llm_intent_draft",
        lambda *_args, **_kwargs: intent_draft(
            sector="Industrials",
            grounding={
                "country_code": None,
                "sector": "industrial",
                "industry": None,
                "investment_horizon": None,
                "objectives": {},
                "topics": {},
            },
        ),
    )

    with pytest.raises(ResearchClarificationNeeded, match="candidate-selection"):
        build_research_plan("hunt for an underappreciated industrial name", settings(tmp_path))


def test_contextual_stateside_omission_cannot_reuse_wrong_prior_country(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    no_model = Settings(tmp_path, tmp_path / "db.sqlite", None, "test-model", "desktop.workspace")
    prior = build_research_plan("study Canadian biotech stocks", no_model)
    monkeypatch.setattr(
        "portfolio.research_planner._llm_intent_draft",
        lambda *_args, **_kwargs: intent_draft(route="refine_screen"),
    )

    with pytest.raises(ResearchClarificationNeeded, match="headquarters country"):
        build_research_plan("stateside names", settings(tmp_path), prior_plan=prior)


def test_llm_only_taxonomy_omission_cannot_run_global_screen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "portfolio.research_planner._llm_intent_draft",
        lambda *_args, **_kwargs: intent_draft(),
    )

    with pytest.raises(ResearchClarificationNeeded, match="industry"):
        build_research_plan("hunt for biotech names", settings(tmp_path))


def test_explicit_nonsemantic_anchors_survive_llm_only_structural_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "portfolio.research_planner._llm_intent_draft",
        lambda *_args, **_kwargs: intent_draft(
            sector="Industrials",
            objectives=("relative_value",),
            grounding={
                "country_code": None,
                "sector": "industrial",
                "industry": None,
                "investment_horizon": None,
                "objectives": {"relative_value": "underappreciated"},
                "topics": {},
            },
        ),
    )

    plan = build_research_plan(
        "hunt for an underappreciated industrial name over the past 30 days "
        "with insider and ownership data for the long term",
        settings(tmp_path),
    )

    assert plan.lookback_days == 30
    assert plan.investment_horizon == "long_term"
    assert {"insiders", "ownership"}.issubset(plan.topics)


def test_general_route_cannot_veto_router_supplied_prior_screen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    no_model = Settings(tmp_path, tmp_path / "db.sqlite", None, "test-model", "desktop.workspace")
    prior = build_research_plan("study biotech stocks", no_model)
    monkeypatch.setattr(
        "portfolio.research_planner._llm_intent_draft",
        lambda *_args, **_kwargs: intent_draft(route="general", subject_kind="none"),
    )

    plan = build_research_plan("give me 7 stocks", settings(tmp_path), prior_plan=prior)

    assert plan.screen.industry == "Biotechnology & Medical Research"
    assert plan.screen.limit == 7


def test_cheap_candidate_wording_cannot_lose_relative_value_objective(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "portfolio.research_planner._llm_intent_draft",
        lambda *_args, **_kwargs: intent_draft(
            country_code="US",
            sector="Industrials",
            objectives=("positive_signals",),
            grounding={
                "country_code": "American",
                "sector": "industrial",
                "industry": None,
                "investment_horizon": None,
                "objectives": {"positive_signals": "standout"},
                "topics": {},
            },
        ),
    )

    with pytest.raises(ResearchClarificationNeeded, match="candidate-selection"):
        build_research_plan(
            "scout a standout cheap American industrial name",
            settings(tmp_path),
        )
