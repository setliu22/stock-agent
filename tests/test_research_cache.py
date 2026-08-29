from portfolio.research_cache import ResearchClassificationCache


def test_research_classification_cache_is_content_addressed(tmp_path) -> None:
    cache = ResearchClassificationCache(tmp_path / "portfolio.db")
    candidate = {
        "sector": "Technology",
        "industry": "Semiconductors",
        "business_summary": "Builds processors used in data centers.",
    }

    cache.put("Data Centers", candidate, "meaningful", "Explicit infrastructure exposure.")

    assert cache.get(" data   centers ", candidate) == (
        "meaningful",
        "Explicit infrastructure exposure.",
    )
    changed = {**candidate, "business_summary": "Builds consumer mobile applications."}
    assert cache.get("data centers", changed) is None
