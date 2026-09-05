# Repository Instructions

## Product And Scope

Stock Agent is a native macOS stock-research app, not a trading platform or a
general-purpose chatbot. Its two research modes are company/ticker questions and
open-ended trend discovery. Portfolio tracking and seven macro signals provide
supporting context. Only advertise capabilities that the execution path actually
supports; prefer less information over irrelevant or unsupported output.

- Keep UI, research orchestration, validation, calculations, and storage in Swift.
  Python remains only as the optional, read-only LSEG Workspace adapter.
- Prefer Apple Foundation Models for bounded language tasks. Do not reintroduce
  Groq, a cloud login, subscription requirements, or the old Python desktop stack
  without an explicit product decision.
- Preserve the dark navy/lavender palette and native rounded glass styling. The
  Apple Music reference concerns layout and polish, not its colors.
- Keep proposal review and contextual explanations inside the existing window.
  Use crisp native controls/SF Symbols and readable, wrapping text. Check small
  windows, keyboard focus, the multiline caret, and sidebar/title-bar alignment.
- Do not turn conversation details into app copy: licensing assurances, build
  terminology, model-generated slogans, example-question cards, and implementation
  settings are not research results. SEC request identity is configuration, not a
  research capability.
- Preserve the original five explanations in `MacroReference.swift`. Do not
  replace them with invented explanations or silently remove signals.

## Architecture

`native/Package.swift` is the build manifest: Swift tools 6.2, macOS 26 minimum,
with no third-party Swift package dependencies. Use a compatible macOS SDK/toolchain.

| Location | Responsibility |
| --- | --- |
| `native/Sources/StockAgent/` | SwiftUI executable. `StockAgentApp` owns the scene; `AppModel` is `@MainActor @Observable`; `RootView` owns navigation and inline overlays. Page views render state; `Theme.swift` owns shared styling. |
| `native/Sources/StockAgentCore/Domain.swift` | Shared models, errors, proposals, financial facts, and portfolio records. |
| `ResearchPlanner.swift`, `ResearchRegistry.swift` in the core | Ticker/theme routing, on-device semantic theme mapping, supported industry taxonomy, LSEG screen definitions, and proposal validation. |
| `ResearchEngine.swift`, `InvestmentCase.swift` in the core | Source retrieval orchestration, company-evidence matching, question answering, and evidence-backed financial interpretation. |
| `SECService.swift` in the core | SEC ticker resolution, filings, company facts, and the injectable `DataFetching` transport. |
| `LSEGWorkspaceService.swift`, `scripts/lseg_bridge.py` | Swift-to-Python JSON subprocess boundary. The bridge opens Workspace, retrieves rows, and serializes data; it must not own planning, ranking, or investment advice. |
| `MarketDataServices.swift` in the core | Yahoo daily closing prices and FRED macro series. |
| `PortfolioStore.swift`, `PortfolioImport.swift` in the core | Actor-isolated SQLite, purchase lots, price histories, deterministic JSON/CSV import, and portfolio analytics. |
| `native/Sources/CSQLite/` | System SQLite module shim, not a vendored database implementation. |
| `native/Sources/StockAgentDiagnostics/` | Command-line planner, full research, and macro diagnostics. |
| `native/Tests/StockAgentCoreTests/` | Swift Testing tests and injected fixtures. |
| `scripts/build_native_app.zsh`, `native/Resources/Info.plist`, `assets/` | Release bundle assembly, ad-hoc signing, app metadata, and current icon sources/assets. |

Paths shown without a directory in the table belong to
`native/Sources/StockAgentCore/` unless otherwise stated. `README.md` describes
operation. Check implementation when documentation disagrees with current code.

Research flow: question → validated inline proposal → LSEG/SEC evidence retrieval
→ evidence review and interpretation → report. Theme-to-industry matching is
semantic against a fixed taxonomy, not a hard-coded theme dictionary. LSEG is the
preferred optional company/screen source; SEC is the public filing/facts fallback.
Foundation Models handle language, not authoritative numbers or company identity.
Model and provider availability are runtime conditions, not guaranteed by a build.
Industry suggestions are retrieval scope, not a company eligibility test. Review
producers, suppliers and economically relevant indirect beneficiaries; distinguish
documented capabilities from inferred demand. Candidate discovery is bounded, not exhaustive;
rank product evidence ahead of company size. Named questions can use on-device
query rewriting before filing retrieval, with deterministic fallback.

Apple's model can refuse a request even while its availability is `available`.
Preserve source-only fallback and disclose when a generated answer is unavailable;
do not claim every arbitrary question will receive a synthesized answer. Investment
summaries select verified statements by ID, while company answers validate company
identity, source IDs, and numeric support. Neither check proves every interpretation
correct. Keep default model safeguards and bounded generation.

## Important Commands

Run from the repository root. Open the built app directly; redundant Finder
command wrappers have been removed and should not be recreated.

```sh
swift --version
swift test --package-path native
swift build --package-path native
/bin/zsh scripts/build_native_app.zsh
codesign --verify --deep --strict 'Stock Agent.app'
open 'Stock Agent.app'
```

- The bundle builder performs a release build, stages and verifies an ad-hoc-signed
  bundle, then replaces the local `Stock Agent.app`. Source edits do not update an
  already-built or already-running application automatically.
- Restart the app to inspect a newly built executable.
- The local signing path uses `codesign --sign -`, not iOS provisioning. Do not
  introduce a paid signing dependency for local use. Distribution is a separate
  decision; LSEG still requires the user's own Workspace access/entitlements.

Read-only live diagnostics (availability/network dependent):

```sh
swift run --package-path native StockAgentDiagnostics 'Find companies exposed to autonomous drones'
swift run --package-path native StockAgentDiagnostics --run 'What are META biggest risks?'
swift run --package-path native StockAgentDiagnostics --market
```

The first command plans only; `--run` retrieves data. Diagnostics currently may
report a provider problem in text, so inspect output, not only the exit status.
For visual inspection, the app accepts `--screen Research`, `--screen Portfolio`,
`--screen Market`, `--select AAPL,META`, `--compact-preview` (860×560),
`--expand-signal DFF`, and `--preview-proposal`. The preview
proposal is a UI fixture, not evidence that live theme mapping works.

Optional LSEG setup/check:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
printf '%s\n' '{"operation":"status"}' | .venv/bin/python scripts/lseg_bridge.py
```

Do not recreate an existing environment unnecessarily. Keep Workspace open and
signed in. `.env.example` lists `LSEG_SESSION`, optional `LSEG_APP_KEY`, and timeout
settings. The Swift adapter locates `.venv/bin/python` and the bridge relative to
the project root; moving just the app bundle is not a complete LSEG installation.

## Coding Conventions And Correctness

- Follow existing four-space indentation, Swift naming, small value types, and
  explicit access control. Use `Sendable` at concurrency boundaries; UI mutations
  belong on the main actor, provider/storage state in actors. Do not put blocking
  subprocess or network work on the main actor.
- Keep calculations and validation in `StockAgentCore`, not SwiftUI views or
  model prompts. Reuse `StockTheme`/shared components instead of per-view styling.
- Use `async`/`await`, bounded requests, explicit unavailable/error states, and
  dependency injection. Existing seams include `ThemeMapping`, `DataFetching`,
  `LSEGResearchProviding`, `CompanyFitEvaluating`, and `CompanyQuestionAnswering`.
- Validate exact identifiers and evidence provenance across model boundaries.
  Never attach a generated answer to a company by array position or invent a
  metric, citation, filing quotation, or explanation to fill missing results.
- Industry membership or a counterparty mention does not establish company
  exposure. Theme exposure does not establish investment merit. Answer the actual
  company question rather than substituting an unrelated investment pitch.
- Preserve financial units, period start/end, filing dates, and source labels.
  Do not mix quarter/YTD/annual periods, sum incomplete debt components as total
  debt, call negative P/E cheap, or present analyst targets as promised returns.
  Peer comparisons need genuinely comparable companies and sufficient data.
- Portfolio accounting covers open purchase lots, split adjustment and unrealized gains,
  not sales, realized P/L, dividends, fees, taxes, or a cash ledger. Do not
  label it a complete brokerage-account or total-return calculation.
- Preserve smooth graph interpolation, percentage axes, 1M/3M/1Y/All controls,
  single/multiple holding selection, and fixed red/green ticker-icon scales.
  Charts and summaries use return on actual purchase cost; ranges are viewports,
  not rebasing. Preserve raw transactions; split-adjusted quantities are derived.
  Purchases must not appear as performance gains; missing history is not a flat
  zero-return observation. Require real purchase dates and finite numeric inputs.
  Do not restore manual current-price overrides or mix unverified legacy histories
  with automatically retrieved, split-adjusted prices.
- Keep bridge stdout exclusively JSON. Send diagnostics elsewhere, handle missing
  fields/nonfinite provider values, and keep all bridge operations read-only.

## Testing Requirements

- Run `swift test --package-path native` for logic changes, then build the release
  app with `scripts/build_native_app.zsh` before calling an app change complete.
  Do not run concurrent SwiftPM jobs against the same scratch/build directory.
- Use Swift Testing (`import Testing`, `@Test`, `#expect`) and injected fixtures;
  unit tests must not require live Workspace, network access, or Apple Intelligence.
  Add focused regression tests for the behavior being changed. Use temporary
  SQLite databases, never the user's portfolio.
- Cover ticker routing (including `$meta`), semantic theme mapping, broad-sector
  false positives, SEC counterparty attribution, exact source excerpts, financial
  period/debt/valuation validity, missing provider data, the original five macro
  references, malformed imports, SQLite round trips, and contribution-neutral
  portfolio/range calculations when touching those areas.
- For provider/model changes, separately run relevant live diagnostics when
  available. Report what was actually exercised and any unavailable dependencies;
  a fixture test or cached result is not a live integration test.
- For UI changes, inspect the rebuilt app at its minimum 860×560 window and a
  larger size. Check Research submission/review, wrapping/caret/focus, sidebar,
  explanations, empty/loading/error states, chart ranges and multi-selection.
- For shell/Python changes, use `zsh -n <script>` and
  `.venv/bin/python -m py_compile scripts/lseg_bridge.py`; verify the JSON bridge
  status separately when Workspace is available. There is no active Python GUI
  test suite in the native architecture.
- Run `git diff --check` and inspect the final diff/status before handoff. A
  documentation-only change does not require repackaging the app; distinguish
  existing build failures from regressions introduced by the task.

## Local Data, Cleanup, And Shared Worktrees

- Begin by reading `git status --short`, diffs, and recent commits. Use reflog and
  history to investigate suspected overwrites; do not assume another agent caused
  them. Preserve uncommitted work and coordinate file ownership with other agents.
- `AppConfiguration.databaseURL()` checks `STOCK_AGENT_DB`, then existing
  repo/bundle-relative `data/portfolio.db` locations, then Application Support
  (`Stock Agent/portfolio.db`). Settings also use `UserDefaults`. Never reset or
  migrate the real database merely to make a test or screenshot convenient.
- Do not expose or commit `.env`, credentials, portfolio records, provider logs,
  or local sessions. Raw LSEG research is kept in memory, not the portfolio DB;
  shared/cloud retention requires a separate design and licensing review.
- Persist purchase records, not market caches. Price histories and splits are
  session-only; HTTP disk caching and provider file logging are disabled.
- Treat `.venv/`, `native/.build/`, built app bundles, caches, logs, and recovery
  backups as local artifacts, not source. Historical commits include tracked
  build/backup artifacts: verify with `git ls-files` and stage specific paths,
  never assume `.gitignore` makes already tracked files safe.
- Verify callers, imports, scripts, resources, and packaging before deleting
  legacy code. Keep the LSEG bridge and icon-generation sources: they still serve
  the native app. Do not resurrect obsolete GUI/Groq/Supabase files from history
  simply because old tests or backups mention them.
- Commit/push only when requested, and never stage unrelated work. Update this
  document when architecture, commands, or core product invariants change.

## Migration And Recovery Notes

The native audit on 2026-09-05 started from clean commit `28fb14d7` ("Astra
changes"); the inspected history contained no revert of that saved work. Recheck
current history rather than treating this historical observation as a current
worktree guarantee.

Legacy source is recoverable from Git history. Do not recreate migration backup
folders in the repo. The native test suite
replaces the obsolete Python GUI/research tests. Do not restore arbitrary ratings,
partial-debt aggregation, undocumented portfolio date defaults, or hidden database
fallbacks to satisfy old expectations.

## GitHub And Network Diagnostics

- Treat failures from `gh`, `git`, package registries, and other networked tools
  under default sandbox permissions as potentially caused by restricted network
  access.
- If `gh auth status` reports an invalid token in the sandbox, rerun the same
  read-only check with network escalation before diagnosing expired credentials
  or asking the user to authenticate again.
- Do not recommend GitHub CLI login, refresh, or logout commands based only on
  a sandboxed authentication check.
- Ask the user to reauthenticate only when the escalated check also reports an
  authentication failure.
- Apply the same escalation-first diagnostic rule when an important GitHub or
  dependency command fails with DNS, connection, or other likely network-access
  errors.
