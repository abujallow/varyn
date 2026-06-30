# Varyn Elevation Specification

This file records the approved operating decisions for Varyn's tiered elevation. It is the source of truth for future implementation sessions. Varyn remains local-first, zero-cost, and risk-intelligence focused. Work proceeds one verified tier at a time.

## Default Watchlist

Varyn's heartbeat will watch these tickers by default:

- TSLA
- F
- GM
- NVDA
- JPM
- BAC
- MTB (M&T Bank)

The watchlist must live in editable configuration rather than application logic.

## Proactive Notices

Varyn should interrupt only for meaningful risk events:

- A watched ticker moves more than 5% intraday.
- A watched ticker moves more than 8-10% over a rolling five-day period.
- A watched company's overall risk score crosses above 70.
- A company already above 70 increases by at least five risk points.
- Major market or index stress appears, including a broad sector selloff, volatility spike, or financial-sector stress.
- A live-data failure affects a requested analysis and could mislead the user.
- File or document analysis finds a major contradiction, missing data, or material risk flag.

Varyn should quietly log normal price movement, small risk-score changes, routine market updates, low-confidence signals, data refreshes, minor missing yfinance fields, and other informational but non-urgent events.

## Quiet Hours

Quiet hours are 22:00-08:00 in the user's local time.

During quiet hours, normal notices accumulate silently. Varyn may interrupt only for a truly critical event, such as an 8-10% watched-ticker move, an overall risk score reaching 80 or higher, a provider or system failure affecting active work, or monitoring the user explicitly requested during an active session.

## Actions Requiring Confirmation

Varyn must obtain explicit confirmation before it:

- Writes, modifies, moves, or deletes files outside approved Varyn session and upload folders.
- Deletes memory, uploads, logs, or project data.
- Exports, emails, uploads, or sends user data off the machine.
- Changes API keys, provider configuration, environment files, or billing-related settings.
- Runs terminal, PowerShell, desktop, or system commands.
- Opens, closes, installs, uninstalls, or modifies applications.
- Makes financial, investment, banking, or credit decisions for the user.
- Presents preliminary risk analysis as final investment advice or a final credit opinion.
- Uses uploaded documents outside their active session.
- Commits, pushes, deploys, or publishes code.
- Accesses browser or system controls beyond the agreed Varyn environment.

Varyn may analyze, explain, summarize, and recommend next steps without confirmation. It must ask before actions that change files, systems, configurations, data, or external outputs.

## Voice Defaults

- Push-to-talk is the default input mode.
- Open-mic may be offered later as an optional mode.
- Reliability comes first, followed by transcript clarity and then open-mic autonomy.
- Speech output uses the browser's default voice.
- No British or London voice is forced.
- A preferred installed local voice may become an optional configuration later.

## Reasoning Models

- Primary model: `openai/gpt-oss-20b:free`
- Fallback: one free OpenRouter model configured through `OPENROUTER_FALLBACK_MODEL`
- Try the primary model first.
- Use the fallback only when the primary is rate-limited, unavailable, or errors.
- If both fail, enter clearly labelled local offline mode.
- Never imply that a provider succeeded when it failed.
- Model names belong in environment or agent configuration, not scattered through application logic.

## Tier Order

1. Unified tool-calling agent core
2. Durable long-term memory
3. Real local telemetry
4. Proactive heartbeat
5. Confirmation, audit, configuration, and kill-switch rails
6. Voice reliability hardening

Each tier must preserve the current working baseline and pass its verification before work begins on the next tier.

## Verified Progress

- Tier 0: operating decisions captured.
- Tier 1: unified registered-tool agent loop verified.
- Tier 2: durable long-term memory verified across process restarts, manual edits, and file-context isolation.
- Tier 3: real psutil telemetry verified through the backend, Next.js proxy, HUD polling, and an active multi-company analysis load test.
- Tier 4: proactive heartbeat verified for configured thresholds, quiet-hour deferral, held notices, restart-safe scheduling, no-overlap execution, deduplication, and dismissal.
- Tier 5: confirmation, injection defense, centralized config, persistent audit, proactive kill
  switch, and richer heartbeat notices verified.
- Tier 6: browser voice reliability verified at the transcript/agent boundary. Push-to-talk is the
  default, open mic remains optional, typed input remains independent, transcripts are visible,
  speech interruption is local, and failed/empty captures never reach the agent.

## Tier 6 Voice Reliability

- Hold the configured push-to-talk key or HUD control to capture speech; release finalizes and
  submits only a usable text transcript through the same `processCommand()` path as typed input.
- The most recent recognized transcript remains visible beside Varyn's reply so recognition and
  reasoning failures can be distinguished.
- Speech synthesis pauses recognition, and starting a new push-to-talk turn cancels current speech
  before listening. Stop-speaking phrases remain local and never require a provider request.
- Open mic is optional and returns cleanly to standby after permission or capture failures.
- Browser Web Speech remains the only required implementation. Whisper STT and Piper TTS are
  documented as disabled future local seams and are not installed.

## Pre-Tier-5 Refinement

- Stable viewport-anchored desktop HUD with internally scrolling response and activity surfaces.
- Clean status-rail spacing for the Heartbeat label and value.
- Reopenable proactive-notices panel with a local four-second carousel using the existing heartbeat data.
- End-to-end OpenRouter response streaming through FastAPI and Next.js.
- Background session-memory writes, condensed activity events, and selective durable-fact retrieval.
- Verified with lint, a production build, Python syntax checks, live backend/proxy probes, and browser interaction checks.
- The desktop core remains at identical coordinates before and after a streamed reply; response growth is contained by internal scrolling.
- Tier 5 remains not started.

## Pre-Tier-5 Market Ticker Refinement

- Replaced the corner proactive-notices card with a reserved, full-width market ticker row.
- The ticker reads the heartbeat's persisted batch snapshot through the existing heartbeat poll; it adds no market polling or outbound data request.
- Each configured watchlist symbol appears once per marquee cycle with cached price, daily move, and honest unavailable states.
- Heartbeat notices remain available to the activity and backend notice systems without adding alert controls to ticker segments.
- Continuous ticker movement is a CSS animation independent of heartbeat refresh timing and pauses on hover or keyboard focus.
- Verified OpenRouter/tool health, psutil telemetry, durable memory, heartbeat data, structured analysis, desktop anchoring, mobile fit, lint, production build, and a clean browser console.
- Tier 5 remains not started.

## Pre-Tier-5 S&P 500 Ticker Expansion

- Removed inline risk badges and dismiss controls from ticker segments. Every segment now uses the same symbol, cached price, and color-coded daily-move presentation.
- Added an editable local `agent/data/sp500.json` constituent file containing 503 current S&P 500 securities. Runtime scans do not fetch or rewrite the constituent list.
- Extended the existing heartbeat worker rather than adding another polling loop. The watchlist remains prioritized, while the broader index refreshes in 40-symbol yfinance batches with a six-second delay between batches and a configurable ten-minute full-refresh target.
- Cached index results are written locally and atomically. Failed symbols retain their last-known quote and are marked stale rather than receiving fabricated values.
- The heartbeat endpoint provides a lightweight 35-symbol ticker payload: the seven pinned watchlist symbols plus a time-rotating 28-symbol S&P window. The browser duplicates that single cycle once only for a seamless CSS marquee, avoiding a 500-node animation.
- Heartbeat alerts, thresholds, held notices, and dismissal remain limited to the configured watchlist. Broader S&P names are market-awareness data only.
- The market tool reads simple S&P quote questions from the local heartbeat cache, including source timestamp, stale status, and a preliminary/not-financial-advice label. Detailed risk and ratio requests retain the richer yfinance path.
- Added support for the tagged tool-call format emitted by some free OpenRouter models so internal tool markup is parsed instead of displayed in the HUD.
- Verified 503 cached constituents, seven pinned watchlist symbols, 28 rotating broader symbols, per-cycle deduplication, uniform ticker styling, KO cache lookup, durable memory, structured Tesla analysis, OpenRouter/tool health, psutil telemetry, Python compilation, lint, production build, viewport anchoring, and a clean browser console.
- Tier 5 remains not started.
