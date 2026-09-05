# Stock Agent

Stock Agent is a native SwiftUI macOS application for company research, thematic
stock discovery, portfolio tracking, and macro context.

## What it does

### Research

Enter either a ticker-based question, such as `What are META's biggest risks?`,
or an open-ended investment theme, such as `Find companies exposed to autonomous
drones`.

Before a run, an inline research plan shows the resolved ticker or the industries
matched to the theme. The plan can be edited and submitted without opening a
second window.

For thematic discovery, Apple Intelligence translates the request into supported
industry universes and product-level filing language. LSEG Workspace screens the
selected industries when it is connected. Swift then verifies the returned
company descriptions against those product terms so broad sector membership or a
counterparty mention is not presented as company exposure.

Each discovery result contains:

- the source language explaining why the company surfaced;
- a natural-language investment view;
- evidence-backed reasons to continue researching it;
- counterpoints and missing evidence; and
- compact fundamentals and expandable source excerpts.

Ticker research combines available LSEG company data with SEC filings and company
facts. The on-device model answers the exact question from that retrieved evidence.

### Portfolio

Purchases are stored in local SQLite. The portfolio view automatically retrieves
daily history, supports 1M, 3M, 1Y, and All ranges, and redraws for any single or
multiple selected holdings. The portfolio line removes new purchase contributions
from performance; selected ticker lines compare normalized market-price returns.
Ticker icons use a fixed red-to-green return scale. JSON portfolio import, manual
price entry, and price CSV import remain available.

### Market

The Market view loads these five FRED signals:

- effective federal funds rate;
- Federal Reserve assets;
- CPI inflation;
- U.S. high-yield option-adjusted spread; and
- VIX.

Selecting a signal expands the original reference explanation, including when it
matters and the company characteristics it tends to favor.

## Data sources

- **LSEG Workspace** — preferred company fundamentals and industry screens. It
  uses the signed-in desktop session and the installed LSEG Python library because
  LSEG does not provide a Swift data-library SDK.
- **SEC EDGAR** — company resolution, financial facts, and annual filings.
- **FRED** — macro series.
- **Apple Foundation Models** — on-device semantic planning and bounded research
  synthesis.
- **Daily market prices** — portfolio chart history, with CSV/manual fallback.

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

The former Python desktop bundle is retained only as a backup under `backups/`.

## Build and run

Double-click:

```text
Start Stock Agent.command
```

To test and rebuild the local app, double-click:

```text
Update Stock Agent.command
```

The native local build is ad-hoc signed and does not require an Apple Developer
Program membership. Xcode or the free Command Line Tools must provide Swift.

Run tests directly with:

```bash
swift test --package-path native
```

Keep LSEG Workspace open and signed in to use LSEG-backed research.

## Local data

Portfolio data is stored in `data/portfolio.db` when the repository-local database
exists. The SEC request identity and application settings use local macOS storage.
Secrets in `.env` are excluded from version control.
