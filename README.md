# Stock Agent

## Root-cause research-pipeline correction

The executor now distinguishes three different outcomes correctly: a successful response with rows, a successful response with no rows, and an LSEG error. A valid empty response is no longer recursively split into smaller field requests. Field isolation is used only when LSEG explicitly reports invalid field syntax.

Row-expanding content is also sequenced correctly. Sector research first completes the broad screen, core finalist data, histories, Reuters evidence, peer context, metrics, and finalist ranking. Only after a leading candidate exists does the application request optional guidance, events, ownership, and insider context for that one company. Fund ownership uses a narrow daily snapshot window rather than an unconstrained holder table. These optional datasets can enrich the report, but they cannot block the core investment research workflow.

## Request timeout and stop control

Each LSEG HTTP request now uses `LSEG_REQUEST_TIMEOUT`, defaulting to 20 seconds. Slow optional evidence such as ownership is skipped after one timeout rather than recursively generating more requests. Ownership is retrieved as a bounded current snapshot for the top finalist, and insider activity is limited to a one-year quarterly window.

During research, the Send button remains active. Hovering over `Researching...` changes it to `Stop research`; clicking it requests cancellation. The workflow stops at the next safe checkpoint, or when the current LSEG request returns or reaches its timeout. The progress footer shows the active request elapsed time against that timeout.

## Sector-screen reliability fix

Natural-language sector wording is now canonicalized before any LSEG request. For example, `industrial`, `industrials`, and `industrial sector` all become the TRBC sector `Industrials`, which is screened with `TR.TRBCEconSectorCode` value `52`. The executor follows LSEG's documented `discovery.Screener` pattern first and retains the full `SCREEN(...)` expression only as a compatibility fallback.

A request such as `Can you do some research on a potential bargain buy in the industrial sector?` now selects the `sector_opportunity` workflow rather than a company deep dive.

## Live deep-research progress

Deep LSEG research now reports its actual workflow while it runs. The interface shows a 0 to 100 percent progress bar, the current stage, elapsed time, the active LSEG API request, screened-universe counts, shortlist creation, and finalist-by-finalist deep dives. The chat transcript contains one live status block that is updated in place rather than flooding the conversation with duplicate messages.

A local macOS stock-research application using LSEG Workspace, the LSEG Data Library for Python, and an optional Groq model.

## Research architecture

The language model does not invent LSEG functions, fields, tickers, or research procedures. It performs two narrow tasks:

1. Classify the request into a predefined workflow and extract constraints.
2. Synthesize opportunities and risks from data already retrieved by the workflow.

The deterministic workflow compiler supports:

- `company_deep_dive`
- `company_compare`
- `sector_opportunity`
- `stock_screen`
- `market_news`

For a request such as `research a promising industrials stock`, the application:

1. Builds a broad LSEG industrials universe.
2. Retrieves value, quality, cash-flow, expectations, analyst-target, momentum, and risk fields.
3. Uses a coverage-aware multi-factor ranking.
4. Deeply researches five finalists using comparable core data, histories, Reuters evidence, peers, filings, and ESG.
5. Re-ranks finalists using deep-dive evidence coverage.
6. Enriches only the selected leader with bounded guidance, event, ownership, and insider context when entitled.
7. Gives the evidence package to the LLM to identify major opportunities, catalysts, risks, and contradictions.
8. Runs a second claim-checking pass and returns a short plain-text report.

The deterministic fallback produces the same report structure when Groq is unavailable.

## LSEG capability awareness

Installation generates:

```text
data/lseg_capabilities.json
```

It contains:

- 17 explicitly executable, read-only operations used by the workflows.
- 111 curated LSEG capability families.
- The five workflow definitions and their stages.
- An inventory of all non-private functions and classes found in the installed `lseg-data` package, including signatures and docstrings.

Specialized operations such as custom-instrument writes, derivative pricing, curves, surfaces, low-level endpoints, real-time streams, Tradefeedr, and bulk delivery are catalogued but are never invoked from casual natural language.

## Install or replace the project

Keep the `.env` file in your existing `stock-agent` folder, then run the rebuild package supplied with this release. The rebuild preserves `.env`, `.git`, and `data/portfolio.db`, removes the obsolete application files, creates a new `.venv`, installs dependencies, exports the capability inventory, runs tests, and rebuilds `Stock Agent.app`.

Keep LSEG Workspace open and signed in while running research.

## Test

Double-click:

```text
Test LSEG.command
```

The diagnostic prints the selected workflow, screen expression, API-call trace, and final concise report.

## Examples

```text
research a promising industrials stock
analyze Palantir
compare Nvidia and AMD
screen the top 12 US technology companies above $10B with forward P/E below 40
show Apple's suppliers, filings, ownership, and news
what LSEG functions are available for volatility surfaces?
```

## Logs

```text
data/stock_agent_install.log
data/stock_agent_gui.log
data/lseg-data-lib.log
```
