# VARYN — Build Roadmap & Source of Truth

This file is the single source of truth for what's left to build on Varyn and the order to build
it in. Any future Codex / Claude Code session should read this file (and `VARYN.md`) before
starting work. Build one item at a time. Verify each before the next. Never fuse items together.
Everything stays free and local — no paid tools, no paid APIs, no subscriptions. No commit, push, or
deployment without the owner's explicit confirmation. Keys (when ever needed) live only in `.env`
files, never in code or chat.

---

## Where we are now

- **Tier 0** (interview + VARYN.md spec) — DONE
- **Tier 1** (unified tool-calling agent core + registry + free-model fallback) — DONE
- **Tier 2** (durable long-term memory, separate from session/file context) — DONE
- **Tier 3** (real local telemetry via psutil; honest N/A) — DONE
- **Tier 4** (the heartbeat — proactive watchlist monitoring) — DONE
  - Refinement passes also DONE: permanent centering / no layout shift, heartbeat label fix,
    streaming responses, selective memory, non-blocking logging.
  - Ticker work DONE: corner notices panel replaced by a full-width scrolling market ticker;
    decluttered to uniform segments; expanded to full S&P 500 awareness (cached, batched,
    rotating-window display; watchlist symbols pinned).
- **Data-layer Phase 1** (keyless audit store + yfinance/Stooq validation + confidence +
  source health) — DONE
  - The live Stooq endpoint currently presents a JavaScript browser-verification gate to
    `pandas-datareader` on this machine. Varyn reports Stooq as unavailable and lowers confidence;
    it does not claim validation succeeded. Deterministic non-persisted tests verify both the
    Stooq fallback/source-change branch and material-disagreement `Flagged` branch.
- **Data-layer Phase 2** (keyless SEC EDGAR official fundamentals + source health) — DONE
  - Official ticker-to-CIK resolution, companyfacts/XBRL mapping, slow heartbeat-owned refresh,
    audit storage, confidence/discrepancy handling, and credit/liquidity integration verified.
- **Data-layer Phase 3** (FRED macroeconomic/risk context + source health) — DONE
  - Eleven configurable official series, heartbeat-owned caching, dated confidence, macro answers,
    and company-risk context alongside SEC fundamentals verified.
  - Source-health reconciliation is DONE: SEC/FRED rows now derive status from their real cached
    subsystem refresh evidence instead of remaining `Awaiting` when request counters are empty.
- **Public-backend security hardening** — DONE LOCALLY; HOSTED VERIFICATION PENDING
  - Vercel-to-Render shared-secret authentication, server-asserted demo/owner roles, Upstash-backed
    anonymous quotas, owner-login throttling, protected controls/tools, sanitized public health,
    scoped browser sessions, and streaming upload ceilings are implemented.
  - Python security regression tests, ESLint, dependency audits, and the Next.js production build
    pass. Vercel Preview/Production Redis and Render secret verification remain before completion.
- **Tier 5** (the rails — confirmation, injection defense, config, audit, kill switch) — DONE
  - Consequential actions require exact per-action approval in the HUD; approval never carries
    forward to another action.
  - Uploaded content and durable memory are treated as untrusted data, proactive behavior can be
    paused independently of conversation, and heartbeat notices include expandable risk context.
- **Tier 6** (browser voice reliability hardening) — DONE
  - Push-to-talk is the default; open mic is optional; typed and voice transcripts share the same
    agent path; heard text is visible beside the response.
  - Recognition pauses during speech output, push-to-talk interrupts a reply, and failed or empty
    captures remain local and never reach the agent.
- **Tier 7, feature 1** (exportable single-company risk memo in Markdown + HTML + PDF) — DONE
  - Deterministic yfinance, SEC EDGAR, FRED, and local risk-engine evidence is separated from the
    optional provider narrative, with source/date/confidence provenance on every quantitative row.
  - Export is protected by the Tier 5 per-action confirmation gate and recorded in the persistent
    audit trail. All three formats are delivered to the HUD for immediate browser download; local
    files are optional audit copies and are not required for delivery.
- **Provider-resilience hardening** (free OpenRouter multi-model chain) — DONE
  - Catalog-validated free models, bounded retries/backoff, quiet failover, actual-model HUD
    reporting, persistent diagnostics, and full-chain local degradation are implemented behind the
    existing provider seam.
- **Tier 7 risk-memo polish + architecture self-knowledge fix** — DONE
  - Shareable memo dates are human-readable, deterministic confidence includes its data-quality
    rationale independently of the narrative, quantitative provenance is validated before export,
    and Varyn accurately identifies OpenRouter as its current reasoning provider.
- **UX & voice refinement** (source health, telemetry cleanup, TTS, fullscreen, command deck) — DONE
  - Stooq remains an honest independent price validator; speech, controls, and HUD hierarchy were
    refined without adding paid services, request latency, or a second interaction path.
  - Voice pacing and character plus the emerald-on-black brand recolor are DONE; sentence and
    paragraph pauses are configurable, Multilingual Natural voices lead the free fallback chain,
    and the existing HUD geometry and animation system are unchanged.
  - The follow-up vanta-black refinement is DONE: green is now semantic-only, cool dark-blue depth
    and crisp neutral detail drive the HUD, and a subtle CSS starfield enriches the orbital field.

## The decision that drives ordering

The layered data strategy (below) is **the foundation the rails tier sits on**. Tier 5 is about
confidence, audit trail, and honesty about uncertainty — which is exactly what multi-source
validation and confidence scoring provide. So **Data-layer Phase 1 is built BEFORE Tier 5.** The
remaining data phases are additive (just more sources plugging into a confidence system that
already exists), so they come AFTER the tiers.

---

## Remaining build order

1. **Data-layer Phase 1 — foundation (keyless): local data store + source-health monitor +
   confidence scoring + Stooq backup. — DONE.** See "Layered Data Strategy" below.
2. **Tier 5 — the rails — DONE:** hard confirmation gate on consequential actions; treat all
   external/file/web content as data, not commands (prompt-injection defense); config over
   hardcode; a real persistent audit trail; a kill switch for all proactive behavior.
   - Include here: make surfaced **heartbeat alerts expandable into a short risk readout**
     (the move, a quick risk read, and an offer to run a full analysis) instead of a bare
     one-line notice.
3. **Tier 6 — voice reliability hardening (free, browser-based) — DONE:** push-to-talk default,
   keep the typed path alive, visible transcript, don't-listen-to-itself, let-me-interrupt.
   Optional free local seams (Whisper / Piper) only as non-required upgrades.
4. **Tier 7 — beyond the baseline (stretch):** exportable risk memo (Markdown + HTML + PDF with
   in-browser download) — DONE; richer fundamentals, real document intelligence, glanceable face,
   and always-on home remain unstarted.
5. **Data-layer Phase 2 — SEC EDGAR** (official fundamentals/filings; keyless) — DONE.
6. **Data-layer Phase 3 — FRED** (macro/risk context; free API key, no cost) — DONE.
7. **Data-layer Phase 4 — regulatory & enforcement signals** (official keyless CFPB complaint
   aggregates; OCC/Federal Reserve feasibility documented for later increments) — DONE.
8. **News & sentiment layer** — last, once the core data engine is stable.

### Future enhancement — bank-specific fundamentals mapping

- **Bank/financial-institution-specific fundamentals mapping** — NOT STARTED. Generic corporate
  fields such as total debt, current assets, current liabilities, and current ratio are often not
  meaningful or directly mapped for banks such as MTB. A dedicated future pass should map deposits,
  loan-to-deposit ratio, Tier 1 capital, net interest margin, and other institution-appropriate SEC
  filing metrics. This is a roadmap note only; no implementation belongs in the current pass.

---

## Layered Data Strategy (the "onion" — 8 layers)

Core principle: **use different sources for different types of truth; no single source is
responsible for everything.** yfinance stays the convenient primary feed, surrounded by free /
official layers that validate, back up, and add context. Varyn stores, validates, scores, and
explains the reliability of the combined data.

- **Layer 1 — Primary market data:** `yfinance` (daily OHLCV, ETFs, indices, watchlist, snapshots).
  Convenient but not an official production contract — validate important outputs.
- **Layer 2 — Backup price history:** Stooq via `pandas-datareader` (cross-check close/adj-close/
  volume; fallback when yfinance fails). **Keyless.**
- **Layer 3 — Official fundamentals:** SEC EDGAR / Companyfacts API (10-K/10-Q, XBRL facts).
  Strongest free source for US public-company fundamentals. Raw — needs a mapping layer. Pull on a
  slow cycle (filings update quarterly/annually). **Keyless.**
- **Layer 4 — Macro / economic risk:** FRED (fed funds, 10Y & 2Y treasuries, 10Y–2Y spread, CPI &
  core CPI, unemployment, initial jobless claims, GDP, industrial production, sentiment, mortgage
  rates, stress indicators). Context for company/sector/portfolio risk. **Free API key.**
- **Layer 5 — Supplemental fundamentals/ratios:** Alpha Vantage, Finnhub, FMP — selective only,
  cached, never the core (free tiers are limited). **Free keys.**
- **Layer 6 — News & sentiment:** headlines/metadata only (licensing-aware — summarize, don't store
  full articles). Context layer, not a truth layer; confirm major events against SEC/official
  releases.
- **Layer 7 — Local storage & caching:** reduce repeat calls, preserve historical snapshots, work
  through outages, create an audit trail of what Varyn knew and when. Store raw response + cleaned
  data + source + timestamp + ticker/series + data type + refresh frequency + confidence + error
  logs + last successful pull + fallback used.
- **Layer 8 — Confidence scoring:** every major output carries a confidence level —
  **High** (official source, or multiple sources agree, fresh + validated),
  **Medium** (one reliable source, not cross-checked, or minor in-tolerance differences),
  **Low** (single unofficial source, stale, incomplete, or post-error),
  **Flagged** (sources disagree materially, missing fields, data looks wrong, or too stale).

## Recommended priority order (from the strategy doc)

1. Keep `yfinance` as primary daily market data.
2. Improve local database + caching first (before adding many APIs).
3. Add Stooq as backup price validator.
4. Add FRED as macro risk layer.
5. Add SEC EDGAR as official filings/fundamentals layer.
6. Add confidence scoring across all outputs.
7. Add source-health monitoring.
8. Add Alpha Vantage / Finnhub / FMP only for selective supplemental use.
9. Add news & sentiment after the core data engine is stable.
10. Only ever consider paid providers if Varyn needs production-grade / high-volume data later.

> Note on our build order vs. the doc's list: we pull **storage + source-health + confidence +
> Stooq into Phase 1 together** so confidence scoring has a real second source to compare against
> from day one, and so Tier 5's rails are built on top of it. FRED and SEC follow as their own
> phases after the tiers.

## What Varyn should NOT do

Call every source for every ticker every day; treat free APIs as unlimited; assume one source is
always correct; treat yfinance as an enterprise contract; waste limited API calls; store only
cleaned data without raw responses; give risk outputs without confidence; build many integrations
before storage/caching are stable.

## What Varyn SHOULD do

Pull yfinance daily; cache everything locally; validate prices with Stooq where practical; use FRED
for macro; use SEC EDGAR for official fundamentals; use limited APIs selectively; track source
health; score confidence; flag disagreement; explain uncertainty clearly; prefer free, official,
reliable sources.

---

## Operating rhythm (applies to every item above)

### Data-layer Phase 1 verification

- Added a bounded, git-ignored per-symbol audit trail under `agent/data/market_store/` containing
  raw source responses, normalized values, source, pull timestamp, ticker, data type, configured
  refresh frequency, confidence, last successful pull, errors, and fallback source.
- Added keyless Stooq support through `pandas-datareader`, including Python 3.12 / pandas 3
  compatibility handling, one-hour result/failure caching, and no extra heartbeat loop.
- Watchlist checks and on-demand market questions use validation; broad S&P 500 batches remain
  polite single-source pulls and are recorded without triggering 500 Stooq requests.
- Confidence behavior verified: yfinance + unavailable backup returns `Low`; forced yfinance
  failure selects Stooq with a source-change note; a 28.57% deliberate mismatch returns `Flagged`.
- `/source-health`, `/health`, and the existing `/heartbeat` payload expose source status. The HUD
  displays compact yfinance/Stooq health through the existing heartbeat refresh.
- Verified the watchlist, 503-name S&P cache, 35-symbol deduplicated ticker window, structured risk
  analysis, durable-memory registration, psutil telemetry, viewport anchoring, clean browser
  console, Python compilation, lint, and production build.
- Data-layer Phase 1 was completed before Tier 5 as required. Tier 6 and later data phases remain
  not started.

### Tier 5 verification

- Added a hard confirmation boundary between model-selected consequential actions and tool
  execution. The HUD states the exact pending action and exposes `Approve once` / `Deny`; live
  approval, denial, non-preauthorization, and unchanged-memory checks passed.
- Added prompt-injection defense for uploaded content and durable facts. A planted file instruction
  was flagged as untrusted data and surfaced to the user without executing or requesting the
  destructive action.
- Consolidated runtime values in `agent/varyn.config.json`, including models, ports, heartbeat
  thresholds, quiet hours, watchlist, voice mode, confirmation actions, and injection patterns.
  A live threshold change loaded without a code edit and was restored to its configured default.
- Added a persistent, git-ignored JSONL audit trail for tool use, heartbeat events, confirmations,
  data source/confidence, and model-request latency. Audit payloads redact user content and secrets;
  development records were sanitized while preserving event metadata and tallies.
- Added a proactive kill switch. Live HUD testing confirmed heartbeat work pauses while ordinary
  conversation remains available, and monitoring resumes without restarting the agent.
- Upgraded heartbeat notices into expandable risk readouts with move/window, plain-language risk
  context, source/confidence, per-notice dismissal, and a structured-analysis action.
- Verified the viewport-anchored HUD has no response-time layout shift, the browser console is
  clean, OpenRouter remains active, all tools are registered, psutil telemetry is live, the
  seven-name watchlist and 503-name S&P cache remain intact, Python compilation passes, and both
  lint and production build pass.
- Tier 5 was completed before Tier 6 as required. Tier 6 verification follows; Tier 7 and later
  data phases remain not started.

### Tier 6 verification

- Replaced the continuous-listening-first behavior with a browser voice controller that defaults to
  push-to-talk using the configured `Space` key or hold control. Open mic remains an explicit,
  optional mode.
- Typed commands and finalized voice transcripts enter the same `processCommand()` function and
  frontend proxy. No second agent loop or voice-specific reasoning path was introduced.
- Added a persistent `Heard` transcript surface beside Varyn's reply. Empty or unclear captures are
  rejected locally and display an actionable message without creating a chat or model request.
- Preserved `sanitizeForSpeech()` and local stop-speaking interception. Speech synthesis pauses
  recognition; beginning push-to-talk while Varyn is speaking cancels queued/current speech before
  recognition starts.
- Hardened permission and capture failures: open mic disables cleanly, does not restart-loop, and
  the error banner no longer blocks mode, typed-input, or safety controls.
- A voice-sourced text transcript sent through the Next.js proxy invoked the market-data tool for
  KO and returned an OpenRouter-backed market response. A TSLA structured-analysis regression also
  passed.
- The embedded verification browser denied microphone and speech-synthesis access. The denied and
  empty capture paths produced zero backend chats and zero model requests; physical microphone and
  audible-output quality remain part of the owner's local Chrome/Edge acceptance check.
- `/health` reports OpenRouter active and 12 tool/status registrations. psutil telemetry, durable
  memory, heartbeat, the seven-symbol watchlist, 503-name S&P cache, confidence/source data, Tier 5
  rails, and structured analysis remain active. Lint and production build pass.
- Whisper STT and Piper TTS are documented only as disabled future local seams; neither was
  installed or made a runtime requirement.
- Tier 7 and all later data phases remain not started.

### Data-layer Phase 2 verification

- Added a keyless SEC EDGAR companyfacts client with a configurable descriptive User-Agent,
  polite request throttling, and no paid provider or API key.
- Added an editable bootstrap ticker-to-CIK file plus a cached official SEC map. The live official
  refresh loaded 10,433 entries; watchlist symbols and broader S&P names including `MTB`, `TSLA`,
  `NVDA`, `KO`, `CAT`, `WMT`, and `BRK-B` resolved successfully.
- Mapped supported XBRL facts into revenue, net income, assets, liabilities, debt, cash, current
  assets/liabilities, operating cash flow, and shares outstanding. Every available value retains
  its form, filing date, period end, accession, source, and confidence; unsupported facts remain
  explicitly unavailable.
- Verified MTB from its SEC-filed 2026-05-05 10-Q: total assets `214,736,000,000 USD` and net
  income `664,000,000 USD`, both High-confidence official figures. The local fallback also reports
  these facts correctly when the free reasoning model is temporarily unavailable.
- SEC metadata refresh runs inside the existing heartbeat worker once daily; fundamentals refresh
  weekly or when a new filing appears. All seven watchlist companies were cached, and an immediate
  repeat returned `not_due` with zero checks and no new network work.
- SEC raw and mapped results are stored under the existing git-ignored data layer. Source health
  reports SEC active with zero observed errors, and the HUD status rail now includes SEC EDGAR.
- Verified a deliberate material mismatch is `Flagged` and retains the official SEC value. The
  structured MTB credit/liquidity analysis includes SEC filing source, form, date, and confidence.
- `/health` reports OpenRouter configured and all existing tools plus official fundamentals;
  heartbeat, 503-name S&P ticker data, telemetry, durable memory, rails, audit, confidence, and
  structured analysis remain active. Python compilation, frontend lint, and production build pass.
- Tier 7 and all later data phases remain not started.

### Data-layer Phase 3 verification

- Added a free FRED adapter that reads `FRED_API_KEY` only from the local environment. A focused
  scan confirmed the key is absent from source, cache, market-store records, and audit output.
- Added eleven editable macro series in `agent/varyn.config.json`: effective fed funds, 10-year
  and 2-year Treasury yields, 10Y-2Y spread, CPI, core CPI, unemployment, initial claims, GDP,
  industrial production, and consumer sentiment.
- Extended the existing heartbeat worker rather than adding a scheduler. FRED refreshes every six
  configured hours; an immediate repeat returned `not_due`, checked zero series, and made zero new
  requests.
- Cached raw and normalized observations in the existing git-ignored Phase 1 store with series ID,
  observation date, pull time, direction, source, release-frequency freshness, and confidence.
- Live verification refreshed all 11 series without errors. The required macro question returned
  fed funds `3.63%` (`DFF`, observation 2026-06-26) and the 10Y-2Y spread `0.28` percentage points
  (`T10Y2Y`, observation 2026-06-29), sourced from FRED with High confidence.
- Added a registered cached-only `macro_context` tool and local fallback. The live Next.js proxy
  returned an OpenRouter-backed macro answer with series IDs, observation dates, source,
  confidence, cache timestamp, and preliminary/not-financial-advice framing.
- MTB structured analysis now combines High-confidence SEC 10-Q fundamentals with High-confidence
  FRED policy-rate and yield-curve context. Macro data informs the narrative and does not
  mechanically alter local risk scores.
- Registered FRED in source health and the HUD data-health rail. `/health` reports OpenRouter,
  market/risk/memory tools, SEC, FRED, rails, audit, and kill switch active; the seven-name
  watchlist, 503-name S&P ticker cache, telemetry, heartbeat, and prior analysis behavior remain
  intact.
- Python compilation, frontend lint, production build, HUD HTTP response, backend endpoints, and
  frontend proxy checks pass. Phase 4, the news layer, and Tier 7 remain not started.

### UX & voice refinement verification

- Kept Stooq as the independent daily-price validator and changed the HUD to render each source's
  own `Active`, `Degraded`, `Unavailable`, or awaiting state. Its current public endpoint remains
  gated on this machine, so the persisted live status correctly reads `Unavailable` and price
  confidence remains reduced rather than implying a successful cross-check.
- Removed GPU and temperature rows from SYS MONITOR. CPU, memory, network, uptime, process count,
  and OS remain sourced from the existing live psutil endpoint; no values are simulated.
- Expanded backend and frontend speech-only normalization. Verified `$10B` becomes `ten billion
  dollars`, `$5M` becomes `five million dollars`, `$214.736B` becomes `two hundred fourteen
  billion dollars`, and `10Y-2Y` becomes `ten-year minus two-year`; markdown, backslashes,
  parentheses, and stray punctuation are removed without changing displayed text.
- Added silent, startup-only selection of a high-quality installed English voice, preferring local
  Microsoft Natural/neural voices. There is no per-response lookup, provider call, setup event, or
  added processing path; unavailable premium voices fall back immediately to the browser default.
  Piper remains a documented disabled future seam and was not installed.
- Added a native fullscreen icon control. The same viewport-anchored HUD enters fullscreen without
  changing the grid and tracks native `fullscreenchange`; browser Escape exits the native mode.
- Kept Send, voice mode, hold/open-mic control, Stop speaking, and the monitoring kill switch
  prominent. Relabelled the Tier 5 control to `Pause Monitoring` / `Resume Monitoring` with a
  tooltip; Clear file, Reset session, and Clear analysis are grouped under `More`; Focus prompt was
  removed.
- Speech-normalizer examples, preferred-voice selection, JSX parsing, project ESLint, and the
  production Next.js build pass. Existing agent, heartbeat, S&P ticker, telemetry, memory,
  confidence/source-health, SEC, FRED, rails, audit, kill switch, and analysis paths remain intact.
- Phase 4, the news layer, and Tier 7 remain not started.

### Tier 7 feature 1 verification — exportable risk memo

- Added the registered `export_risk_memo` action for explicit single-company memo requests. The
  deterministic preflight resolves the ticker and freezes the exact Markdown and HTML paths before
  the Tier 5 gate presents `Approve once` / `Deny`; no memo file exists before approval.
- Verified an approved MTB export under `agent/data/memos/`. Market evidence is sourced from
  yfinance, official fundamentals from SEC EDGAR companyfacts, macro observations from FRED, and
  structured scores/drivers/actions from the existing local risk engine. Quantitative rows carry
  source, relevant date, and High/Medium/Low/Flagged confidence; missing SEC fields say
  `Not available` rather than being inferred.
- The analyst narrative is visually and structurally separated from deterministic evidence. Its
  qualitative-only integrity guard withholds provider output containing numeric claims. When both
  free OpenRouter routes were unavailable during verification, the complete memo still exported
  with an honest narrative-unavailable note.
- Markdown and vanta-themed HTML files were generated, and the persistent audit trail recorded the
  company, generation time, sources, exact paths, narrative status, confirmation resolution, and
  confirmed execution. The memo directory remains git-ignored.
- Updated `/health` reports OpenRouter configured, all eight registered tools, active yfinance,
  SEC EDGAR, and FRED sources, and the Markdown/HTML memo capability. Python compilation, ESLint,
  and the Next.js production build pass; prior heartbeat, telemetry, memory, S&P ticker, rails,
  analysis, SEC, and FRED behavior remains unchanged.
- PDF export and browser delivery were completed in a later focused pass. No other Tier 7 feature,
  Phase 4, or news-layer work was started.

### Voice pacing and emerald visual refinement

- Added configurable sentence and paragraph pacing (`280 ms` / `560 ms`) with a slightly calmer
  configured speech rate (`0.96`). The frontend now segments the formatted reply before speech-only
  cleanup, queues one utterance per sentence, keeps recognition suspended through breath pauses,
  and preserves immediate stop/interruption behavior.
- Updated the configurable free voice order to Microsoft Andrew, Ava, and Brian Multilingual
  Online (Natural), then the existing Online Natural choices, any other en-US Natural voice, and
  finally legacy/default. Candidate probing remains startup-only with no per-reply lookup or log.
- Recolored the existing HUD through emerald theme variables and RGB channels: near-black surfaces,
  emerald accents, high-contrast neutral typography, lime gains, and retained red losses. No DOM,
  grid, spacing, sizing, panel, orb, ticker, or animation structure changed.
- Browser verification at `1280x720` found zero blue-dominant computed colors, no viewport overflow,
  unchanged three-column geometry, and active grid, perimeter, ticker, star, sweep, orbit, pulse,
  core, and waveform animations. Gain (`rgb(163, 230, 53)`), loss (`rgb(255, 111, 139)`), and
  accent (`rgb(52, 211, 153)`) remain distinct.
- OpenRouter and all 14 tool/status registrations remain active. The seven-name watchlist,
  503-name S&P cache, psutil telemetry, SEC, FRED, heartbeat, rails, audit, and analysis remain
  intact. Python compilation, speech segmentation/priority tests, ESLint, production build, and
  the current HUD console pass.
- Audible voice character remains the owner's local Edge/Chrome speaker acceptance check because
  the embedded verification browser does not expose Web Speech audio.
- Phase 4, the news layer, and Tier 7 remain not started.

Build → run the item's verification → STOP → owner verifies by hand → owner says proceed. Keep a
pre-change safety snapshot before large refactors. Keep `/health` green (OpenRouter active, tools
registered) and lint/build passing after every item. Free and local always.

### Natural voice and source-health visibility refinement

- Made the installed neural voice preference configuration-driven. The ordered default is
  Microsoft Ava, Emma, Andrew, Brian, then Aria Online (Natural), followed by any other en-US
  Natural voice and only then a legacy/default voice.
- Voice discovery now waits for asynchronous `voiceschanged`/window-load population, performs one
  short startup playback probe, caches the successful voice, and falls through failed candidates.
  Ordinary responses do no voice lookup or setup logging and therefore add no selection delay.
- Added speech-only removal of raw ISO timestamps and UUID-like machine strings. Both `$214.736B`
  and the live SEC response form `$214.74 billion USD` normalize to `two hundred fourteen billion
  dollars`; display text remains unchanged.
- Removed Stooq only from the visible DATA HEALTH list. `/health` still registers `yfinance +
  Stooq` validation and reports Stooq's live background status, so fallback and confidence logic
  are unchanged. The HUD now shows yfinance, SEC EDGAR, and FRED.
- Python compilation, speech-normalizer tests, live public-config checks, ESLint, and production
  build pass. `/health` reports OpenRouter and all 14 tool/status registrations; psutil telemetry,
  heartbeat, the seven-name watchlist, 503-name S&P cache, SEC, FRED, rails, audit, and analysis
  remain intact. Audible neural-voice quality remains the owner's local Edge/Chrome acceptance
  check because the verification environment cannot hear system audio.
- Phase 4, the news layer, and Tier 7 remain not started.

### Vanta-black visual and starfield refinement

- Replaced the ambient emerald skin with a predominantly near-black palette, restrained cool
  dark-blue depth, pale blue-white structural detail, and high-contrast neutral typography.
- Restricted green to functional meaning: Online, market gains, active source/core states,
  ready-file text, and active voice-mode text. Computed-style verification found no green panel
  backgrounds; market gains remain green and losses remain red.
- Added two CSS-only starfield layers to the existing orbital field. Small pale particles drift at
  slow `42s` and `58s` cycles behind the HUD with no JavaScript loop or additional dependency.
- Preserved the exact viewport geometry: `1259x699` stage, `220px` left rail, `631px` core,
  `360px` right rail, and `94px` command deck at `1280x720`, with no page overflow or layout shift.
- Verified grid, perimeter, ticker, original stars, sweeps, orbits, helix, pulses, core breathing,
  waveform, and new starfield animations remain active. The live HUD console is clean.
- `/health` reports OpenRouter and all 14 tool/status registrations. The seven-name watchlist,
  503-name S&P cache, psutil telemetry, SEC, FRED, heartbeat, rails, audit, and analysis remain
  intact. ESLint and the production Next.js build pass.
- Phase 4, the news layer, and Tier 7 remain not started.

### SEC/FRED source-health display fix

- Reconciled the source-health tracker with authoritative SEC EDGAR and FRED subsystem evidence:
  cache counts, last refresh/check timestamps, configured state, and recorded refresh errors.
- Successful cached subsystems now report `Active`; partial refreshes with retained data report
  `Degraded`; unconfigured, empty, or failed-without-cache states remain honestly unavailable or
  awaiting. yfinance and background Stooq tracking are unchanged.
- A forced free FRED refresh completed `11/11` with no errors. SEC correctly returned `not_due`
  under its unchanged slow cadence. Both remained Active afterward with nine SEC symbols cached.
- `/health`, `/heartbeat`, and `/source-health` all return yfinance, SEC EDGAR, and FRED as Active.
  Browser verification confirms the HUD displays those same three Active rows at `100%`.
- OpenRouter and all 14 tool/status registrations remain active; telemetry, heartbeat, memory,
  S&P ticker, confidence, SEC/FRED data, rails, audit, analysis, and voice behavior remain intact.
  Python compilation, deterministic state tests, ESLint, production build, and HUD console pass.
- Phase 4, the news layer, and Tier 7 remain not started.

### Provider-resilience hardening verification

- Replaced the single fallback with a configurable, explicitly free chain: gpt-oss 20B,
  gpt-oss 120B, Qwen3 Next Instruct, then `openrouter/free`. Runtime validation uses a cached copy
  of OpenRouter's official model catalog and skips entries that are missing, paid, or unable to
  satisfy tool-call requirements.
- Added configurable per-request timeout, per-model retry count, short backoff, total-attempt cap,
  and total wall-clock budget. Attempt reservation guarantees every remaining model, including the
  final free router, retains one try before Varyn declares the chain exhausted.
- Live invalid-primary verification skipped the nonexistent model and returned through
  `openai/gpt-oss-120b:free` with the actual model and one failover reported. The configured
  gpt-oss 20B primary was rate-limited during the final live check; deterministic tests confirm a
  healthy primary returns after exactly one request with no failover overhead.
- Forced all-model failure returns the existing honest local/offline result with local tools
  intact. The existing MTB memo generated during a full provider outage retains all deterministic
  market, SEC, FRED, and risk sections and labels its narrative unavailable.
- Persistent audit records now distinguish requested and served models, attempts, latency,
  eligibility skips, and failovers. The live activity log remains quiet, and the HUD continues to
  take its model label from the actual response; public config also retains the last served model.
  No API key or key length is printed or logged.
- `/health` reports OpenRouter configured, the four-model free chain, a cached official catalog,
  all eight registered tools, the risk memo capability, and active yfinance/SEC/FRED sources.
  Python compilation, ESLint, and the Next.js production build pass. Heartbeat, telemetry, memory,
  S&P ticker, confidence/source health, SEC, FRED, rails, analysis, voice, and memo behavior remain
  intact.
- Phase 4, the news layer, and all other Tier 7 features remain unstarted.

### Tier 7 memo polish and self-knowledge verification

- Memo presentation now formats timestamps as human-readable UTC date-times and filing/observation
  dates as human-readable calendar dates in Markdown, HTML, and PDF. Raw ISO values remain unchanged
  in caches, tool payloads, filenames, and audit records.
- Added a deterministic pre-export provenance validator. Every available quantitative evidence row
  must carry a source, relevant date, and High/Medium/Low/Flagged confidence; unavailable rows must
  remain explicitly `Not available` and `Flagged`.
- Structured risk confidence is calculated before narrative generation and includes a plain-language
  rationale. The live MTB memo correctly remained Low because Stooq validation was unavailable and
  generic SEC mapping lacks material bank credit/liquidity fields, not because of narrative state.
- The Tier 5 gate created no files before approval. The approved MTB export produced Markdown and
  HTML with no raw displayed ISO timestamps, a provider-composed narrative from the primary
  `openai/gpt-oss-20b:free` model, no figures in that narrative, and persistent memo/confirmation
  audit events.
- Strengthened Varyn's provider identity instructions. A live OpenRouter-backed question returned:
  `Yes, I’m connected through OpenRouter as the current reasoning provider.` Varyn no longer claims
  that conversational reasoning is fully local or independent of OpenRouter.
- Recorded **Bank/financial-institution-specific fundamentals mapping** as a future enhancement only.
  No bank-specific SEC mapping, Phase 4, news layer, or other Tier 7 feature was built.
- `/health` reports OpenRouter configured, the free fallback chain, all eight registered tools, the
  memo capability, and active yfinance/SEC/FRED sources. Python compilation, ESLint, and the Next.js
  production build pass; all prior heartbeat, telemetry, memory, ticker, source-health, rails,
  analysis, voice, provider-resilience, and memo behavior remains intact.

### Tier 7 PDF and browser-delivery verification

- Added ReportLab PDF generation with the same deterministic market, SEC EDGAR, FRED, structured
  risk, Analyst Narrative, and provenance sections as the existing Markdown and HTML memo.
- ReportLab `4.2.5` was verified as a prebuilt pure-Python wheel for Linux CPython 3.11. A rendered
  two-page landscape memo passed visual inspection with readable tables, page numbers, separated
  interpretation, and no clipping or overlap.
- Approved exports return Markdown, HTML, and PDF as base64 response artifacts with filename, MIME
  type, and size. The HUD exposes MD / HTML / PDF controls that create local browser Blob downloads;
  no user-facing response contains a Render container path.
- Local memo writes remain optional audit copies. A forced ephemeral-disk failure still returned all
  three browser artifacts and recorded the failed local-copy status in the persistent audit event.
- The Tier 5 per-action confirmation gate remains unchanged and still executes before generation.
  Delivery failure is reported plainly, without presenting an inaccessible server path.
- Python compilation, ESLint, and the Next.js production build pass. Phase 4, the news layer,
  bank-specific fundamentals, and all other Tier 7 features remain unstarted.

### Data-layer Phase 4 verification — regulatory & enforcement signals

- Confirmed the official CFPB complaint-search API is keyless and supports exact company filtering.
  Added editable mappings for the full watchlist, including consumer-bank and captive-finance
  entities, and an explicit non-applicable result for Nvidia.
- Added a throttled, six-hour cached CFPB adapter comparing current and prior 90-day complaint
  windows. Records retain a bounded local audit payload with the official aggregate, pull/data
  timestamps, confidence, errors, and normalized consumer-conduct readout.
- Added `regulatory_signals` to the unified tool registry and source-health panel. Applicable CFPB
  context now enriches structured operational-risk analysis and appears as dated, attributed,
  confidence-labelled deterministic evidence in Markdown, HTML, and PDF risk memos.
- Complaint volume is explicitly unadjusted for company size and is never framed as proof of
  wrongdoing. Missing/non-applicable data does not create a false zero or an error inference.
- Kept the source on cached manual/on-demand refresh instead of the heartbeat to avoid a second
  background polling burden on the free 512 MB hosting tier.
- OCC enforcement is feasible later through its official search/CSV export and order documents;
  no stable documented public API was identified. Federal Reserve enforcement is a strong future
  candidate through its official keyless recent/historical JSON and CSV datasets. Neither was
  integrated in this increment.
- Phase 4 implementation passed Python compilation, focused CFPB cache/fallback/non-applicability
  checks, live official JPM/MTB aggregate probes, memo provenance checks, frontend lint, and the
  production build. Phase 4 stops here; the news layer and other Tier 7 work remain unstarted.
