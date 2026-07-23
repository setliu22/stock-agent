# Stock Agent

## Root-cause research-pipeline correction

The executor now distinguishes three different outcomes correctly: a successful response with rows, a successful response with no rows, and an LSEG error. A valid empty response is no longer recursively split into smaller field requests. Field isolation is used only when LSEG explicitly reports invalid field syntax.

Row-expanding content is also sequenced correctly. Sector research first completes the broad screen, core finalist data, histories, Reuters evidence, peer context, metrics, and finalist ranking. Events, ownership, and insider tables are requested for the winner only when the user explicitly asks for them. Generic guidance expansion is rejected because the available fields do not expose a stable shared record key and can create a Cartesian response. Fund ownership uses a narrow daily snapshot window rather than an unconstrained holder table. These optional datasets can enrich the report, but they cannot block the core investment research workflow.

## Request timeout and stop control

Each LSEG HTTP request now uses `LSEG_REQUEST_TIMEOUT`, defaulting to 20 seconds. Slow optional evidence such as ownership is skipped after one timeout rather than recursively generating more requests. When explicitly requested, ownership is retrieved as a bounded current snapshot for the top finalist, and insider activity is limited to a one-year quarterly window.

During research, the Send button remains active. Hovering over `Researching...` changes it to `Stop research`; clicking it requests cancellation. The workflow stops at the next safe checkpoint, or when the current LSEG request returns or reaches its timeout. The progress footer shows the active request elapsed time against that timeout.

## Sector-screen reliability fix

Natural-language sector wording is now canonicalized before any LSEG request. For example, `industrial`, `industrials`, and `industrial sector` all become the TRBC sector `Industrials`, which is screened with `TR.TRBCEconSectorCode` value `52`. The executor follows LSEG's documented `discovery.Screener` pattern first and retains the full `SCREEN(...)` expression only as a compatibility fallback.

A request such as `Can you do some research on a potential bargain buy in the industrial sector?` now selects the `sector_opportunity` workflow rather than a company deep dive.

Research verbs such as `study`, `examine`, and `assess` also enter the LSEG workflow rather than generic chat. Short, immediately adjacent screen refinements inherit only omitted constraints from the last successful screen: after `study biotech stocks`, `study us stocks` retains the exact biotech TRBC industry and adds U.S. headquarters, while an explicitly named new sector or country replaces the corresponding prior constraint. `all`, `global`, `new screen`, and `start over` requests begin fresh, and unrelated chat closes the refinement context. The normalized trace records the current request, parent request, effective request, and fully compiled screen.

Screen enrichment is authoritative for overlapping value fields because it explicitly requests `Curn=USD`. This prevents a foreign listing's local-currency market capitalization returned by the initial Screener display from overriding the normalized USD value used for filtering, sorting, and reporting.

## Live deep-research progress

Deep LSEG research now reports its actual workflow while it runs. The interface shows a 0 to 100 percent progress bar, the current stage, elapsed time, the active LSEG API request, screened-universe counts, shortlist creation, and finalist-by-finalist deep dives. The chat transcript contains one live status block that is updated in place rather than flooding the conversation with duplicate messages.

A local macOS stock-research application using LSEG Workspace, the LSEG Data Library for Python, and an optional Groq model.

## Supabase account

The Account tab supports Supabase email signup, sign-in, and sign-out. Enter the
Project URL and publishable key from Supabase Project Settings, then choose
**Save connection**. The settings are stored in the local `.env` file; account
passwords are never saved by Stock Agent.

Email authentication and new-user signup must be enabled in the Supabase
project. When email confirmation is enabled, follow the confirmation link
before signing in. Supabase authentication does not by itself upload portfolio
or research data: database tables, Row Level Security policies, and an explicit
sync feature are still required before the app can store data remotely.

The installer adds a current trusted certificate bundle and configures the
packaged application to use it. If the Account tab reports a certificate error,
rerun `Install Stock Agent.command`. Do not disable certificate verification.

The archive-backed cloud portfolio layer is documented in
`README-CLOUD-PORTFOLIOS.md`. Run `supabase/schema.sql` once in the Supabase SQL
Editor before using its REST client; the Account tab currently handles
authentication while the cloud portfolio client remains a tested backend layer.

After the first Git checkout or manual update, double-click:

```text
Update Stock Agent.command
```

The updater refuses to overwrite local source edits, downloads a safe
fast-forward of the current branch, then reinstalls dependencies, runs the test
suite, and rebuilds `Stock Agent.app`. Existing `.env` settings and portfolio
data are preserved.

## Research architecture

Request interpretation is hybrid. A constrained Groq intent pass can resolve wording such as `stateside`, `stands out`, or `underappreciated`, classify a grounded company/universe mention, and recognize requested evidence topics. It returns a strict JSON schema with verbatim current-request evidence for every semantic value. It cannot choose LSEG functions, fields, RICs, screen syntax, API operations, numeric filters, limits, or lookback windows.

The deterministic compiler remains authoritative for explicit country, TRBC sector/industry, company, numeric, horizon, and reset/inheritance constraints, and it derives the executable workflow after reconciliation. Listing-versus-headquarters ambiguity, exclusions, unsupported thresholds, multiple geographies, malformed model output, invented entities, and ungrounded model fields are stopped or discarded before LSEG runs. If the model is unavailable, a fully compiled deterministic request still runs; if neither path can resolve material wording safely, the agent asks a clarification question and makes zero LSEG requests. The normalized trace records accepted semantic fields, rejected generated fields, deterministic conflicts, and the final compiled screen.

Sector screens, stock screens, comparisons, market news summaries, and contextual valuation/risk/catalyst answers are rendered deterministically from validated evidence. The optional model may also help phrase a named-company deep dive, but malformed or misbound report output is discarded.

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
6. Optionally enriches only the selected leader with explicitly requested, bounded event, ownership, or insider context.
7. Produces a selected-RIC-bound deterministic report and follow-up answers from the validated evidence.
8. Persists a sanitized JSONL trace with the normalized plan, exact screen, postcondition counts, request status/duration/rows, RICs, fields, parameters, currency, date windows, and adjustment policy.

The report remains available when Groq is unavailable.

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
data/research_runs.jsonl
```
