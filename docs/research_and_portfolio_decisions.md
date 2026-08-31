# Research and Portfolio Product Decisions

Last reviewed: 2026-08-31

This document records the product decisions behind the Research Lab and Portfolio behavior. It distinguishes what the application implements now from work that would require additional data, accounting rules, or licensing review.

## 1. What "undervalued" means

In this application, undervalued means **peer-relative value evidence**, not an intrinsic-value estimate.

- The research planner can select the typed `relative_value` objective.
- Python then requires at least one usable, positive valuation multiple below the median of the approved LSEG sector or industry universe.
- The ranking can use forward P/E, EV/EBITDA, price/sales, and price/book when applicable and available.
- Growth, profitability, financial resilience, macro fit, and data coverage affect shortlist order, but the LLM does not invent a weighted valuation score.
- Missing factors remain missing. They are not replaced with zero or an estimate.

This is a shortlist rule. The app does not claim that the market price is below a DCF value or that the stock will appreciate.

## 2. Comparing different industries

Raw multiples are not directly ranked across unrelated industries. Each approved universe is screened and ranked independently. For a cross-industry theme such as data centers, the app interleaves candidates by their within-universe rank and then validates thematic exposure from retrieved company profiles. The report names the discovery universe and its peer median for each candidate when that evidence is available.

## 3. Point-in-time data and backtests

Research screens are current retrieval snapshots. The app does not currently store the historical universe membership, delisted companies, estimate vintages, or historical fundamentals needed for a defensible point-in-time backtest. Price-history calculations are valid historical calculations for the securities selected now, but they are not evidence that a historical screen would have selected those same securities.

The UI and reports now state this limitation. A future backtest must use licensed point-in-time universes and vintaged fundamentals before the app can make performance claims without hindsight and survivorship bias.

## 4. Portfolio accounting scope

The current portfolio is an **open-purchase-lot model**. It supports purchases, quantities, purchase prices, purchase dates, current prices, current market value, and unrealized gain or loss.

It does not yet model sales, realized gains, dividends, splits, fees, cash balances, transfers, or taxes. Therefore:

- "Total return" means unrealized return on the recorded open purchases.
- The performance chart is not a complete brokerage-account return.
- Deleting a ticker removes its recorded open purchase lots; it does not record a sale.

The Portfolio `Model` dialog makes this boundary visible. The correct next accounting change is an immutable transaction ledger with typed transaction events, not more fields added to purchase rows.

## 5. Holding objectives and exit conditions

Objectives, expected horizon, and exit conditions are useful because the same facts can matter differently to different owners. They must remain explicitly user supplied and must never be inferred as a "stored thesis."

The position-risk dialog now accepts optional decision context for the current review. It is labeled as user supplied, passed to Groq only for bounded interpretation, and not persisted. Persistent per-position context should be added only with an explicit cloud schema and retention design so local and Supabase behavior remain consistent.

## 6. Evidence freshness

Every Research Lab result now has a generation time and a `Complete` or `Partial` status. Generation time is the retrieval time, not necessarily the reporting period of every fundamental or estimate field. Source reporting periods can differ.

- Current prices and market-regime observations are refreshed when their pages are opened.
- Historical analyses report their actual common observation windows and sample sizes.
- Missing source evidence is shown rather than silently substituted.
- The profile-relevance cache is content addressed. A changed theme, sector, industry, or business summary creates a different key, so an old classification is not reused against changed evidence.

The app does not use an arbitrary global "stale after N days" rule because appropriate freshness depends on the evidence type. Source dates and reporting periods should drive any future per-capability freshness policy.

## 7. Partial research success

Optional capability failures produce missing-evidence warnings. Multi-universe discovery now keeps successful universes when another approved universe returns no match or fails, and labels the result partial. A run still fails closed when required identity, instrument resolution, or comparison evidence is unavailable, because reporting a comparison without its required inputs would be misleading.

Automatic resume is not implemented. A real resume feature requires a durable job record with completed steps and evidence versions; silently rerunning part of an in-memory plan could mix vintages.

## 8. Permanent evaluation scenarios

`tests/test_research_scenarios.py` is the permanent product-level scenario suite. It covers:

- undervalued companies in one supported industry;
- cross-industry thematic discovery;
- exchange geography;
- named-security rate analysis; and
- market-wide news research.

Lower-level tests continue to cover schema validation, instrument grounding, LSEG compilation, thematic classification, calculations, missing evidence, model fallback, and rate limits. A prompt is added to the permanent suite when it represents an intended product workflow or a previously observed regression, not merely because a user happened to type it once.

## 9. Measuring research outcomes

The app does not currently claim that its candidates outperform a benchmark. A defensible evaluator needs to save the approved methodology version, candidate set, selection timestamp, price basis, benchmark, holding horizon, corporate-action treatment, and all candidates considered. It also needs point-in-time input data so results are not contaminated by hindsight.

When those prerequisites exist, evaluation should report excess return, drawdown, volatility, hit rate, and coverage by cohort and methodology version. It should compare the complete generated shortlist, not only names the user later chose to buy.

## 10. LSEG data retention and Supabase

The engineering default is data minimization:

- Raw LSEG result tables, Reuters story text, prices, and fundamentals remain in memory for the active run and are not synced to Supabase.
- Supabase stores user portfolio records defined in `supabase/schema.sql`; it does not store Research Lab evidence.
- Local research diagnostics store a request fingerprint, plan shape, request counts, field coverage, warnings, and instrument identifiers. They do not store the raw user question or LSEG result tables.
- The local profile-relevance cache stores a content hash plus a derived relevance label and short reason. It does not store the raw business summary in a retrievable column.

This is a conservative technical policy, not a legal conclusion. Before distributing the app or adding shared/cloud research history, the LSEG agreement and the user's specific entitlements must be reviewed for display, derived-data, caching, and redistribution rights.

## Model routing

Planning, theme-universe auditing, and final finding selection use Groq's larger `openai/gpt-oss-120b` model when available. Repetitive bounded profile classification uses `openai/gpt-oss-20b` to reduce latency and token pressure. Python validates all model output and remains the authority for identifiers, filters, calculations, state, and execution.
