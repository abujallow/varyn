# Varyn

Varyn is a local-first AI risk intelligence command system with a Next.js HUD and a Python agent backend.

## Architecture

```text
Next.js HUD
  -> Next.js chat/upload/session proxies
    -> FastAPI agent at http://127.0.0.1:8788
      -> unified agent loop
        -> registered market, risk, and active-file tools
        -> OpenRouter primary/fallback reasoning
      -> local session memory
```

OpenAI billing is not required. The agent uses OpenRouter when `OPENROUTER_API_KEY` is configured, tries `OPENROUTER_MODEL` first, and then tries `OPENROUTER_FALLBACK_MODEL`. Gemini remains optional and is not required. If no supported provider is available, Varyn reports local offline mode clearly while keeping local tools available.

## Agent Core

Every typed or transcribed turn enters the same backend agent loop. Capabilities are registered in `agent/tools/registry.py`, and the model may call several tools before answering. Native OpenRouter tool calls are preferred. A strictly parsed JSON action protocol is accepted when a free model does not return native calls. The previous deterministic selection rules remain only as a graceful fallback.

The HUD receives structured analysis only when the `risk_analysis` tool actually returns an analysis object. Conceptual questions therefore remain conversational.

## Durable Memory

Long-term facts are stored in the human-readable, git-ignored file `agent/data/long_term_memory.json`. This store is separate from session history and uploaded-file context. Each entry contains one concise fact with a stable id, and the agent reloads the file for every turn so careful manual edits are respected without rebuilding.

Registered `remember_fact`, `update_fact`, and `forget_fact` tools manage the store. They are reserved for explicit user requests. Durable facts enter the prompt as untrusted background data, never as executable instructions, and uploaded files are never remembered automatically.

## Local Telemetry

The FastAPI agent exposes read-only system measurements at `GET /telemetry` using `psutil`. The Next.js HUD polls the local proxy at `/api/varyn/telemetry` and displays real CPU usage, memory usage, network throughput, process count, operating system, and machine uptime.

GPU utilization and temperature display `N/A` when the operating system does not provide a reliable local sensor through the free monitor. Varyn never substitutes simulated values for unavailable measurements.

## Heartbeat

The FastAPI agent runs a background market-watch service independently from chat requests. Its watchlist, thresholds, interval, timeout, and quiet hours live in `agent/varyn.config.json`. The default watchlist is `TSLA`, `F`, `GM`, `NVDA`, `JPM`, `BAC`, and `MTB`.

The heartbeat batches free yfinance history, calculates latest daily and five-session moves, applies Varyn's preliminary local risk score, and persists its schedule and notices in the git-ignored `agent/data/heartbeat_state.json`. Active-condition fingerprints prevent repeat alerts while a condition remains breached. A slow check cannot overlap its successor.

Routine scans remain in bounded local history. Material notices are held across HUD closures and restarts, deferred during 22:00-08:00 quiet hours unless critical, and exposed through `GET /heartbeat`. The HUD polls the local proxy and renders every surfaced notice with a dismiss control.

## Streaming and Stable HUD

The primary chat path uses Server-Sent Events from OpenRouter through FastAPI and the Next.js proxy. Final-answer tokens render as they arrive; native tool-call deltas remain internal until the selected tools finish. The buffered `/chat` endpoint remains available for compatibility and direct tests.

Session writes are serialized on a background worker after streaming begins. Durable facts are selected by query relevance rather than copied wholesale into every prompt. The desktop HUD is viewport-anchored, with internally scrolling activity and response panels, so new output does not move the Varyn core.

The market ticker reads only the heartbeat's cached snapshot; its CSS marquee adds no market requests or polling loop.

## Official Fundamentals

SEC EDGAR companyfacts is Varyn's keyless official-fundamentals layer. The editable bootstrap
ticker-to-CIK map lives at `agent/data/sec_ticker_cik.json`; a slow scheduled refresh stores the
current official full mapping under the git-ignored `agent/data/sec_edgar/` directory. Requests use
the descriptive User-Agent and throttle configured in `agent/varyn.config.json`.

The mapping layer translates supported XBRL facts into revenue, net income, assets, liabilities,
debt, cash, current assets/liabilities, operating cash flow, and shares outstanding. Every mapped
value retains its form, filing date, period end, accession, source, and confidence. Missing or
ambiguous facts remain unavailable. SEC values anchor company credit and liquidity analysis; a
material conflict with yfinance summary data is flagged while the official filed value is retained.

SEC metadata checks run inside the existing heartbeat worker once per configured day. Companyfacts
refresh weekly or when a newly filed 10-K/10-Q is detected, so chat does not create a fresh SEC
request on every turn. Raw responses, mapped output, health history, and pull audit records remain
local and git-ignored. Status is available at `GET /sec/status`; cached/on-demand fundamentals are
available at `GET /sec/fundamentals/{symbol}`.

## Macroeconomic Risk Context

FRED is Varyn's official macroeconomic context layer. The free `FRED_API_KEY` is loaded only from
the local agent environment and is never written to cache, source-health, audit, or response data.
The editable `fred.series` list in `agent/varyn.config.json` configures policy rates, Treasury
yields and spreads, inflation indices, labor indicators, GDP, industrial production, and consumer
sentiment.

FRED refreshes run inside the existing heartbeat worker on a slow six-hour cadence. Chat and risk
requests read only the git-ignored `agent/data/fred/macro_snapshot.json` cache, so they never create
request-time FRED traffic. Each series stores its raw response and normalized observation in the
existing Phase 1 audit store with series ID, observation date, pull timestamp, confidence, and
error history.

The local risk engine uses the cached rates, yield curve, inflation, and labor backdrop as labeled
narrative context without mechanically changing company risk scores. Status and cached values are
available at `GET /fred/status`, `GET /fred/snapshot`, and `GET /fred/context`; the HUD source-health
rail includes FRED alongside yfinance, Stooq, and SEC EDGAR.

## Browser Voice Reliability

Browser speech is a thin input/output layer around the same command path used by typed turns. The
default input mode is push-to-talk: hold the configured key or the HUD control, speak, and release
to submit the finalized text transcript. Raw audio is never sent to the FastAPI agent or model.
Open mic remains an optional mode and pauses whenever Varyn speaks so the assistant cannot hear its
own speech. Starting push-to-talk during a reply cancels speech synthesis immediately and begins a
new turn. The latest recognized transcript is shown beside the response for diagnosis.

Voice settings live under `voice` in `agent/varyn.config.json`. Browser `SpeechRecognition` and
`speechSynthesis` remain the free defaults. The config records disabled, optional seams where a
future local Whisper STT or Piper TTS adapter could be connected behind the same transcript/speech
boundary; neither package is installed or required.

The speech-only normalization layer expands financial shorthand such as `$10B`, percentages, and
`10Y-2Y` into natural spoken phrases while leaving displayed responses unchanged. At startup the
HUD silently selects the highest-quality already-installed English browser voice, preferring
Microsoft Natural/neural voices when present; speech begins immediately and falls back to the
browser default without a setup request or conversation log event.

## HUD Controls

The command deck keeps Send, voice mode, hold-to-talk, Stop speaking, and the Tier 5 monitoring
kill switch prominent. Clear file, Reset session, and Clear analysis remain available in the
compact secondary-actions menu. The monitoring control is labelled `Pause Monitoring` and pauses
heartbeat/background work while ordinary chat remains available.

The top status bar includes a native fullscreen toggle. Fullscreen uses the same viewport-anchored
HUD grid, exits through the control or Escape, and does not create a second layout. The system
monitor displays only real browser-visible psutil metrics: CPU, memory, network, uptime, process
count, and OS. Source-health rows display each source's own Active, Degraded, or Unavailable state;
Stooq remains the independent daily-price validator and is reported honestly when its public
endpoint is gated.

The approved elevation decisions and tier order are recorded in `VARYN.md`.

## Local Preview

Start the Python agent in the first PowerShell window:

```powershell
cd C:\varyn\agent
powershell -ExecutionPolicy Bypass -File .\start-varyn-agent.ps1
```

The backend defaults to `http://127.0.0.1:8788`.

Start the HUD in a second PowerShell window:

```powershell
cd C:\varyn
npm.cmd run build
powershell -ExecutionPolicy Bypass -File .\preview-varyn.ps1
```

The preview script chooses an available local port starting at `3200`.

## Provider Setup

Copy the agent environment example:

```powershell
cd C:\varyn\agent
copy .env.example .env
```

Configure OpenRouter without committing the local `.env` file:

```text
OPENROUTER_API_KEY=
OPENROUTER_MODEL=openai/gpt-oss-20b:free
OPENROUTER_FALLBACK_MODEL=openrouter/free
```

The Next.js proxy reads `VARYN_AGENT_URL` from `C:\varyn\.env.local`. The local default is:

```text
VARYN_AGENT_URL=http://127.0.0.1:8788
```
