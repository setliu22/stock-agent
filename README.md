# Stock Agent

Stock Agent is a native SwiftUI macOS application for company research, thematic
stock discovery, portfolio tracking, and macro context.

## What it does

### Research

Enter either a ticker-based question, such as `What are META's biggest risks?`,
or an open-ended investment theme, such as `Find companies exposed to autonomous
drones`.

The Research composer shows **Company Research** and **Trend Discovery**. Select a
workflow to make the intent explicit, or leave both unselected for automatic
routing. LSEG and Apple Intelligence status appear alongside research, with a
connection refresh control.

Before a run, an inline research plan shows the requested ticker or the industries
matched to the theme. The plan can be edited and submitted without opening a
second window.

For thematic discovery, Apple Intelligence translates the request into supported
industry universes and product-level filing language. LSEG Workspace screens the
selected industries when it is connected. Swift then verifies the returned
company descriptions against those product terms so broad sector membership or a
counterparty mention is not presented as company exposure.

Discovery reviews bounded industry-screen or filing-search candidates; it is not
an exhaustive search of every public company. Incidental and unverified matches
are omitted even when that means fewer results than requested. SEC fallback
searches filing text rather than enforcing the LSEG industry filters.

Each discovery result can contain:

- the source language explaining why the company surfaced;
- a concise financial interpretation when comparable evidence is available;
- evidence-backed reasons to continue researching it;
- counterpoints and missing evidence; and
- compact fundamentals and expandable source excerpts.

Ticker research combines available LSEG company data with SEC filings and company
facts. The on-device model answers the question from retrieved evidence, with
company/source identity and numeric checks. If synthesis fails, the app either
shows a short source-bound risk summary, quotes related filing passages, or
explicitly reports that it could not generate an answer. Investment evidence is included for investment/valuation
questions, not appended to every question. These are research findings, not buy
ratings or return forecasts. Runs can be cancelled.

Apple Intelligence can decline source summarization even when enabled. The app
keeps the retrieved evidence available and identifies that fallback. On-device
query rewriting helps translate everyday wording into filing terminology; model
output is not a substitute for checking the source.

### Portfolio

Purchases are stored in local SQLite. The portfolio view automatically retrieves
daily history, supports 1M, 3M, 6M, 1Y, and All ranges, and redraws for selected
holdings. Charts and summaries show unrealized return on actual purchase cost;
ranges change the visible dates, not the cost basis. Split adjustments preserve
the original transactions. Dividends, sales and fees are not modeled. Prices are
retrieved automatically; missing or stale quotes are not invented or manually overridden.

### Market

The Market view loads these FRED signals:

- effective federal funds rate;
- 10-year Treasury yield;
- Federal Reserve assets;
- CPI inflation;
- unemployment;
- U.S. high-yield option-adjusted spread; and
- VIX.

Selecting a signal expands the original reference explanation, including when it
matters and the company characteristics it tends to favor.
The policy summary describes observed rate and Fed-asset changes, not an automated
investment stance. Each comparison identifies its comparison date.

## Data sources

- **LSEG Workspace** — preferred company fundamentals and industry screens. It
  uses the signed-in desktop session and the installed LSEG Python library because
  LSEG does not provide a Swift data-library SDK.
- **SEC EDGAR** — company resolution, financial facts, and annual filings.
- **FRED** — macro series.
- **Apple Foundation Models** — on-device semantic planning and bounded research
  synthesis.
- **Daily market prices** — automatically retrieved portfolio history and stock splits.

LSEG is optional. Its path requires an existing Workspace subscription and
entitlements; SEC, FRED, portfolio tracking, and local on-device features continue
to work when Workspace is unavailable. Raw LSEG research results remain in memory
for the active run and are not written to the portfolio database.

## Architecture

The application UI, state, storage, research orchestration, validation,
calculations, and report rendering are Swift. `scripts/lseg_bridge.py` is a small
standalone read-only adapter around LSEG's supported desktop Python library; it
does not import the former Python research stack or make planning or ranking
decisions.

The installed application is built from `native/` and placed at:

```text
Stock Agent.app
```

Legacy Python GUI/research sources and migration backups have been removed.
The optional LSEG adapter and current icon sources remain.

## Build and run

Open `Stock Agent.app` directly. After source changes, run
`zsh scripts/build_native_app.zsh`, then quit and reopen the app.

The native local build is ad-hoc signed and does not require an Apple Developer
Program membership. Xcode or the free Command Line Tools must provide Swift.

Run tests directly with:

```bash
swift test --package-path native
```

Keep LSEG Workspace open and signed in to use LSEG-backed research.

## Local data

Portfolio data is stored in `data/portfolio.db` when the repository-local database
exists, otherwise in `~/Library/Application Support/Stock Agent/portfolio.db`.
`STOCK_AGENT_DB` can override the path for isolated tests. Database-open errors are
shown without silently switching to an empty database. Existing SEC request
identity settings remain supported without a Settings page.
Secrets in `.env` are excluded from version control.
