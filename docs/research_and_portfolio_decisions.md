# Research and Portfolio Product Decisions

Last reviewed: 2026-09-05

## Research scope

The primary workflows are intentionally narrow: answer a question about a ticker,
or find public companies connected to an open-ended business trend. The research
plan contains only choices that change the run.

Ticker research requires an explicit ticker symbol. Theme research creates a
reviewable inline plan, including the matched industries, before retrieving data.

## Theme matching

There is no table mapping specific themes to sectors. Apple Foundation Models
interprets the theme against a fixed public-equity taxonomy and generates
product-level synonyms likely to occur in company descriptions. For example, the
same generic process that maps a product theme to its end market can distinguish
the end-product industry from enabling software or components.

LSEG screens the approved industries when Workspace is connected. A deterministic
Swift evidence pass then requires product-language support in each returned
description. Broad sector membership alone is not a match. SEC fallback candidates
are checked against their own latest annual filing so language about a customer or
counterparty is not attributed to the filer.

## Investment interpretation

A thematic connection is not treated as proof that a stock is attractive. The
report separates:

- why the company surfaced;
- reasons to continue researching the investment;
- counterpoints and missing evidence; and
- the raw metrics and source excerpts.

When LSEG facts are available, Swift computes comparisons such as trailing and
forward P/E or return on equity versus the median of screened companies with data.
It also evaluates reported cash versus debt and the difference between the source
price and mean analyst target. Apple Intelligence may write a short balanced
synthesis from those evidence lines, but it cannot change identifiers,
classifications, metrics, or citations.

“Constructive” is an evidence summary, not a buy recommendation or forecast. The
app does not claim intrinsic value, guaranteed upside, or theme-attributable
revenue when those inputs are missing.

## Source behavior

LSEG Workspace is the preferred research source and requires the user's existing
session and entitlements. SEC EDGAR remains the company/filing fallback. FRED
provides the five macro series, and daily portfolio prices have CSV and manual
fallbacks.

An unavailable optional source produces an explicit fallback or missing-evidence
state. It is not silently replaced with invented data. LSEG desktop requests are
bounded so a disconnected session cannot leave a research run waiting forever.

## Model boundary

Apple Foundation Models run on device and are used where language understanding is
material: theme interpretation, filing-language expansion, and concise research
synthesis. Swift remains authoritative for source retrieval, ticker identity,
screen construction, numerical calculations, evidence validation, storage, and UI
rendering.

## Portfolio accounting

The portfolio is an open-purchase-lot tracker. It models purchases, quantities,
purchase prices, purchase dates, current value, and unrealized gain or loss. It
does not yet model sales, realized gain, dividends, splits, fees, cash, transfers,
or taxes, so its chart is not a complete brokerage-account return.

The full-portfolio performance index adjusts for new purchase contributions so a
deposit is not shown as a gain. Selecting holdings switches to normalized price
returns for only those tickers, which makes multi-stock comparisons meaningful.
The 1M, 3M, 1Y, and All controls reset the visible comparison at the range start.

## Data retention and distribution

Portfolio records and imported price history are stored in local SQLite. Raw LSEG
research results are kept in memory for the active run and are not written into
the portfolio database. A future cloud or shared-report feature would require a
separate retention and LSEG licensing review.

The local macOS bundle is ad-hoc signed. Distribution through the Mac App Store or
Developer ID notarization would be a separate release decision; neither is needed
to build and run the app locally.

## Regression coverage

Native tests cover ticker routing, theme cleanup, the LSEG industry screen,
product-level fit versus broad sector language, SEC counterparty rejection,
investment evidence construction, the original five macro references, daily price
decoding, portfolio imports, SQLite storage, and selected-history calculations.
