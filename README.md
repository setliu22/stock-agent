# Stock Agent

A local desktop application for human-approved LSEG research, deterministic
industry screening, portfolio tracking, position-risk review, and macro context.

See [research and portfolio product decisions](docs/research_and_portfolio_decisions.md)
for the valuation methodology, accounting boundary, freshness rules,
partial-result policy, evaluation plan, and LSEG retention decisions.

## Main workflows

### Research Lab

Enter a custom equity-research question in the Research Lab. Groq may propose
only capability and analysis IDs from the application's typed registry. Security
and benchmark references must come from the current question; prior chat context
is not silently reused.

The proposal is non-executable. Before any data request, an approval dialog shows
the securities, timeframe, benchmark, data sources, and Python analyses. The user
can edit those inputs and select or remove optional capabilities. The current
macro regime is always attached as standardized context.

The planner supports named-company research, open-ended candidate discovery, and
market-wide Reuters questions. Its visible registry covers company profiles,
financials, profitability, valuation, estimates, analyst opinion, price and
estimate histories, risk, Reuters news, events, ownership, insiders, filings,
peers, suppliers, customers, benchmarks, and rate histories where the backend
has a bounded implementation. It selects capability IDs, never raw LSEG syntax.

For discovery, Groq chooses a visible LSEG sector, industry, or a bounded public-
equity universe; it cannot invent candidate companies. LSEG returns the screen.
When the question contains a business-exposure criterion, Groq may classify that
criterion only against retrieved LSEG business descriptions. Financial, price,
rate, and risk criteria instead use approved LSEG fields and Python analyses.
The approval dialog exposes the universe, result count, retrieval operations,
and calculations. Every question is compiled independently. An invalid model
plan is rejected and retried once with the compiler error; transcript text is
not reused as hidden model context.

After approval, deterministic code resolves instruments and constructs the exact
read-only requests. Python calculates returns, benchmark excess returns,
drawdowns, volatility, estimate changes, daily rate-change correlations, and
falling-rate monthly comparisons with observation counts. Groq receives only the
compact verified finding packet and may select which finding IDs to highlight;
Python renders every factual and numerical statement. If that final model call
fails, the verified deterministic report is still returned.

### Industry research

Open the Market tab and select **Start research**. Choose one supported LSEG
sector or industry and a result count from 1 to 20. The application constructs a
validated `sector_opportunity` plan directly from those controls. No language
model interprets the request or chooses LSEG calls.

The workflow:

1. Screens the selected TRBC peer group.
2. Retrieves comparable growth, profitability, valuation, cash flow, debt,
   estimates, price history, Reuters news, peers, and filings where entitled.
3. Applies deterministic coverage-aware ranking.
4. Deeply researches the finalists.
5. Renders a deterministic evidence report in the Research Lab output.

### Position-risk review

Open the Portfolio tab and select **Review position risk**. Review the full
portfolio or selected holdings. The workflow retrieves consistent company,
valuation, estimate, price, event, and Reuters evidence, then combines it with
the current macro regime.

Quantitative risk signals and ratings are calculated in Python. A bounded Groq
call may classify whether retrieved company-specific news is material and how it
relates to a price move. The model cannot discover instruments, select LSEG
operations, alter scores, or add outside facts.

### Portfolio entry

**Record purchase** supports one manual purchase or a bulk JSON import. Known
JSON structures are parsed deterministically. Groq is used only as a
schema-constrained fallback for unfamiliar key layouts; all required ticker,
quantity, price, and date values are validated before anything is saved.

## Macro data

The Market tab retrieves five indicators without an LLM:

- Effective federal funds rate
- Federal Reserve total assets
- CPI inflation
- U.S. high-yield option-adjusted spread
- VIX

Each row shows the current value, measured change, and a standardized macro tilt:
safer/profitable/low-leverage, neutral, or more tolerant of
high-growth/high-leverage companies. Classification uses historical
distributions and direction, not a manually fixed value copied into the UI.

## Data and security

Portfolio data is stored in local SQLite. When Supabase is configured and the
user is signed in, purchases are synchronized to that user's portfolio. Signing
out clears the disposable local portfolio cache; signing back in restores the
cloud snapshot.

Keep secrets only in `.env`. The repository ignores that file. Account
passwords are never stored by the application.

Run `supabase/schema.sql` once in the Supabase SQL Editor before using cloud
portfolios. Row Level Security limits each signed-in user to their own data.

## Update and open

After the first checkout and for every later update, run:

```text
Update Stock Agent.command
```

This is the only setup command. It uses the files in the current local folder,
refreshes dependencies, runs the test suite, rebuilds the app, and opens it.
It does not fetch, merge, or overwrite anything from GitHub. The generated app
also launches directly from this local folder.

Keep LSEG Workspace open and signed in while running research.

Groq defaults to the current production `openai/gpt-oss-20b` model. Retired
Llama model IDs from older installs are migrated in memory, without rewriting
`.env`. If an explicit custom model returns `model_not_found`, the shared Groq
client retries a bounded list of current structured-output models; other errors
are returned without an unrelated retry.

## LSEG diagnostic

Run:

```text
Test LSEG.command
```

The diagnostic executes the fixed Industrials research workflow and prints the
screen expression, API-call trace, and deterministic report.

## Tests

Run:

```text
Run Tests.command
```

or:

```bash
pytest -q
```

The retained suite covers portfolio calculations and storage, cloud
synchronization, market data and regime classification, exact LSEG plan
validation, request execution, ranking, news relevance, position-risk scoring,
cancellation, and GUI navigation.

## Logs

```text
data/stock_agent_install.log
data/stock_agent_gui.log
data/lseg-data-lib.log
data/research_diagnostics.jsonl
```

Research diagnostics contain plan shape, coverage, request counts, warnings,
and a one-way question fingerprint. They do not contain the raw question or
LSEG result tables.
