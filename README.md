<div align="center">

# Varyn

### AI Risk Intelligence Command System

**By Abubakr Jallow**

[Live Demo](https://varyn-ai.vercel.app) · [Backend Health](https://varyn.onrender.com/health)

</div>

Varyn is a local-first AI risk intelligence command system with a Next.js HUD and a Python agent backend. It turns fragmented public market, fundamental, macroeconomic, and regulatory data into explainable, source-backed risk analysis, cross-validating figures across independent sources and scoring its own confidence in each one. The public deployment has since been hardened with authenticated backend access, bounded demo usage, durable hosted facts, mobile reliability refinements, and an automated regression test suite.

>  **Disclaimer:** Varyn produces preliminary risk analysis for informational and portfolio-demonstration purposes only. It is not financial, credit, investment, or legal advice.

---

## Table of Contents

- [Architecture](#architecture)
- [Public Deployment Security](#public-deployment-security)
- [Hosted Persistence Model](#hosted-persistence-model)
- [Agent Core](#agent-core)
- [Market Data & Price Validation](#market-data--price-validation)
- [Durable Memory](#durable-memory)
- [Local Telemetry](#local-telemetry)
- [Heartbeat](#heartbeat)
- [Official Fundamentals](#official-fundamentals)
- [Macroeconomic Risk Context](#macroeconomic-risk-context)
- [Regulatory & Compliance Signal (CFPB)](#regulatory--compliance-signal-cfpb)
- [Safety Rails](#safety-rails)
- [Exportable Risk Memo](#exportable-risk-memo)
- [Streaming and Stable HUD](#streaming-and-stable-hud)
- [Browser Voice Reliability](#browser-voice-reliability)
- [Mobile HUD Optimization](#mobile-hud-optimization)
- [HUD Controls](#hud-controls)
- [Live Demo](#live-demo)
- [Local Preview](#local-preview)
- [Provider Setup](#provider-setup)
- [Deployment](#deployment)
- [Automated Reliability Tests](#automated-reliability-tests)
- [Roadmap](#roadmap)
- [Limitations](#limitations)
- [Why I Built This](#why-i-built-this)
- [About](#about)
- [License](#license)

---

## Architecture

```text
Next.js HUD (Vercel)
  -> Next.js chat/upload/session proxies
    -> FastAPI agent (Render)
      -> unified agent loop
        -> registered market, risk, and active-file tools
        -> OpenRouter primary/fallback reasoning
      -> local session memory and hosted durable facts
```

## Public Deployment Security

The deployed HUD is a controlled gateway to the Render agent. Vercel signs every backend request
with the server-only `VARYN_PROXY_SECRET`; Render rejects direct access to every endpoint except
the minimal `GET /ping` and sanitized `GET /health` checks. CORS remains defense in depth and is
not treated as authentication.

Anonymous visitors may use bounded chat and read-only market/risk capabilities. The free Upstash
Redis integration applies independent per-IP and per-session hourly/daily limits plus a global daily reserve before a
request can consume an OpenRouter call. If Redis is unavailable in production, anonymous chat and
owner-login attempts fail closed rather than bypassing the limiter.

Uploads, active-file access, durable-memory changes, memo exports, confirmations, session resets,
audit access, heartbeat execution/dismissal, monitoring controls, and forced data refreshes are
owner-only. Owner access uses a signed, `HttpOnly`, `Secure`, `SameSite=Strict` cookie. Vercel
derives the role server-side and Render enforces it again at the route and registered-tool layers.

Required deployment environment variables are:

```text
# Vercel and Render: identical random value
VARYN_PROXY_SECRET=

# Vercel only
VARYN_AUTH_SECRET=
VARYN_OWNER_ACCESS_HASH=  # lowercase SHA-256 of the owner's access key
KV_REST_API_URL=          # supplied by the Vercel Upstash integration
KV_REST_API_TOKEN=        # supplied by the Vercel Upstash integration
```

Optional Vercel tuning variables are `VARYN_CHAT_HOURLY_LIMIT`, `VARYN_CHAT_DAILY_LIMIT`,
`VARYN_GLOBAL_DAILY_LIMIT`, `VARYN_OWNER_LOGIN_LIMIT`, and `VARYN_OWNER_SESSION_SECONDS`.
Defaults are 10 chats/hour and 25/day per IP and browser session, 800 anonymous chats/day globally, five owner-login
attempts per 15 minutes, and an eight-hour owner session.

The upload proxy rejects clearly oversized requests before parsing. FastAPI then streams in
64 KiB chunks, aborts and removes the partial file as soon as the configured 10 MiB ceiling is
crossed, and accepts only the existing document/code/PDF/image allowlist. The maximum lives at
`security.max_upload_bytes` in `agent/varyn.config.json`.

The public HUD displays the anonymous usage boundary clearly: public chat is limited to 10
requests per hour. Owner sessions remain separate from anonymous demo traffic and are intended
only for controlled testing and administration.


OpenAI billing is not required. The agent uses OpenRouter when `OPENROUTER_API_KEY` is configured, tries `OPENROUTER_MODEL` first, and then tries `OPENROUTER_FALLBACK_MODEL`. Gemini remains optional and is not required. If no supported provider is available, Varyn reports local offline mode clearly while keeping local tools available.

In production, the frontend and backend run as two separately deployed services (see [Deployment](#deployment)) rather than on localhost, but the request path is identical, the frontend never calls OpenRouter or any data provider directly, only its own backend.


## Hosted Persistence Model

Varyn separates the local/private installation from the hosted public demo. Local Varyn remains
the authoritative development and private-use environment, while the hosted deployment is designed
as a protected public demonstration with honest persistence boundaries.

On Render, the service filesystem is ephemeral. Varyn therefore treats hosted session history,
temporary uploaded files, heartbeat state, market/SEC/FRED/CFPB caches, and local memo audit copies
as temporary runtime state. That is intentional: uploads should not become permanent public-demo
records, and public data caches can be regenerated.

The exception is explicit long-term remembered facts. In hosted production, durable facts use
Upstash Redis through `KV_REST_API_URL` and `KV_REST_API_TOKEN`, while local development continues
to use the human-readable JSON file by default. `/health/details` reports this persistence model
without exposing secrets. Session memory now prunes stale sessions, and audit logs are capped/rotated
so long-running local or hosted instances do not grow forever.

## Agent Core

Every typed or transcribed turn enters the same backend agent loop. Capabilities are registered in `agent/tools/registry.py`, and the model may call several tools before answering. Native OpenRouter tool calls are preferred. A strictly parsed JSON action protocol is accepted when a free model does not return native calls. The previous deterministic selection rules remain only as a graceful fallback.

The HUD receives structured analysis only when the `risk_analysis` tool actually returns an analysis object. Conceptual questions therefore remain conversational.

## Market Data & Price Validation

Varyn's market tool uses yfinance as its primary live/free price and fundamentals source, with Stooq as an independent daily-price cross-check rather than a single trusted feed. Company-name and ticker resolution runs through a curated alias fast path backed by a broader dynamic lookup, so the watchlist and ad hoc queries both resolve reliably (see [HUD Controls](#hud-controls) for how source status is surfaced, and [Regulatory & Compliance Signal (CFPB)](#regulatory--compliance-signal-cfpb) for where bank-specific data augments this).

## Durable Memory

Durable facts are separate from session history, uploaded-file context, and temporary hosted demo
state. Registered `remember_fact`, `update_fact`, and `forget_fact` tools manage this store and are
reserved for explicit user requests. Durable facts enter the prompt as untrusted background data,
never as executable instructions, and uploaded files are never remembered automatically.

Locally, long-term facts remain stored in the human-readable, git-ignored
`agent/data/long_term_memory.json` file. In hosted production, the same interface is backed by
Upstash Redis so explicitly remembered facts survive Render restarts and redeployments. This keeps
the public demo honest: long-term facts are durable, while session chat history, uploads, caches,
heartbeat state, and temporary runtime files remain intentionally ephemeral.

## Local Telemetry

The FastAPI agent exposes read-only system measurements at `GET /telemetry` using `psutil`. The Next.js HUD polls the local proxy at `/api/varyn/telemetry` and displays real CPU usage, memory usage, network throughput, process count, operating system, and machine uptime.

GPU utilization and temperature display `N/A` when the operating system does not provide a reliable local sensor through the free monitor. Varyn never substitutes simulated values for unavailable measurements.

> In the deployed environment, these metrics reflect Render's Linux container rather than your own machine, this is expected. Memory in particular reads container-wide pressure rather than Varyn's own process footprint, so it sits at a stable, higher baseline than a typical local run.

## Heartbeat

The FastAPI agent runs a background market-watch service independently from chat requests. Its watchlist, thresholds, interval, timeout, and quiet hours live in `agent/varyn.config.json`. The default watchlist is `TSLA`, `F`, `GM`, `NVDA`, `JPM`, `BAC`, and `MTB`.

The heartbeat batches free yfinance history, calculates latest daily and five-session moves, applies Varyn's preliminary local risk score, and persists its schedule and notices in the git-ignored `agent/data/heartbeat_state.json`. Active-condition fingerprints prevent repeat alerts while a condition remains breached. A slow check cannot overlap its successor.

Routine scans remain in bounded local history. Material notices are held across HUD closures and restarts, deferred during 22:00–08:00 quiet hours unless critical, and exposed through `GET /heartbeat`. The HUD polls the local proxy and renders every surfaced notice with a dismiss control.

CFPB regulatory data is deliberately kept outside this heartbeat loop, see below.

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

Bank-specific fundamentals (deposits, loan-to-deposit ratio, Tier 1 capital, net interest margin) are not yet mapped, see [Roadmap](#roadmap). Bank tickers currently return "Not available" for generic corporate ratios rather than a misleading value.

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

## Regulatory & Compliance Signal (CFPB)

The CFPB Consumer Complaint Database is Varyn's fourth data layer, a keyless, cached, regulatory/compliance signal that follows the same provenance pattern as SEC EDGAR and FRED (source, pull timestamp, and confidence recorded alongside every value). Unlike the sources above, it is fetched on-demand only and deliberately kept outside the heartbeat worker, to avoid adding background polling load on top of the existing scheduled refreshes.

Consumer complaint data applies mainly to the watchlist's bank tickers (JPM, BAC, MTB), since it has no equivalent for TSLA, F, GM, or NVDA. It feeds into the same confidence-scoring, risk analysis, audit records, and Exportable Risk Memo output as every other source, rather than through a separate code path.

## Safety Rails

Because Varyn monitors its watchlist proactively and pulls from external data sources, it runs under four safety mechanisms:

- **Confirmation gate**, high-impact or export actions (like generating a memo) require explicit confirmation before they run
- **Prompt-injection defense**, instructions embedded in fetched data or uploaded files are treated as data, never as commands
- **Bounded audit trail**, a JSONL log records actions and decisions for later review and is capped/rotated to prevent unbounded growth
- **Kill switch**, the `Pause Monitoring` HUD control (see [HUD Controls](#hud-controls)) immediately halts heartbeat activity while ordinary chat remains available

## Exportable Risk Memo

Varyn's most decision-maker-facing output: a confirmation-gated, audit-logged export combining deterministic data tables with an LLM-written Analyst Narrative, plus full source/date/confidence provenance for every figure it contains. Available in Markdown, HTML, and PDF (PDF export via ReportLab), with in-browser download buttons for all three formats.

## Streaming and Stable HUD

The primary chat path uses Server-Sent Events from OpenRouter through FastAPI and the Next.js proxy. Final-answer tokens render as they arrive; native tool-call deltas remain internal until the selected tools finish. The buffered `/chat` endpoint remains available for compatibility and direct tests.

Session writes are serialized on a background worker after streaming begins. Durable facts are selected by query relevance rather than copied wholesale into every prompt. The desktop HUD is viewport-anchored, with internally scrolling activity and response panels, so new output does not move the Varyn core.

The market ticker reads only the heartbeat's cached snapshot; its CSS marquee adds no market requests or polling loop.

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


## Mobile HUD Optimization

Varyn's public HUD was refined for mobile without changing the desktop command-center layout.
Below desktop widths, the interface now uses a more intentional stack order, removes nested scroll
traps, reduces the mobile height pressure of the orb while preserving Varyn's visual identity, and
keeps the command input easier to reach.

The browser voice layer also includes mobile-specific reliability hardening. Open mic restarts are
guarded with retry caps, cooldown/backoff behavior, and tab-visibility handling so iOS Safari does
not repeatedly restart speech recognition after every pause and trigger repeated microphone system
tones. The starfield is reduced on narrow screens to limit animation load, and command input sizing
is kept safe for iOS focus behavior.

## HUD Controls

The command deck keeps Send, voice mode, hold-to-talk, Stop speaking, and the Tier 5 monitoring
kill switch prominent. Clear file, Reset session, and Clear analysis remain available in the
compact secondary-actions menu. The monitoring control is labelled `Pause Monitoring` and pauses
heartbeat/background work while ordinary chat remains available.

The top status bar includes a native fullscreen toggle. Fullscreen uses the same viewport-anchored
HUD grid, exits through the control or Escape, and does not create a second layout. The system
monitor displays only real browser-visible psutil metrics: CPU, memory, network, uptime, process
count, and OS. Source-health rows display each source's own Active, Degraded, or Unavailable state.

In the deployed environment, Stooq's status row is currently hidden from this panel, Render's outbound IP is blocked/rate-limited by Stooq's public endpoint consistently enough that surfacing it as a permanent red flag wasn't useful. Source-health tracking for Stooq continues in the background and remains part of the underlying audit data; it's a display-only omission, not a removed check.

The approved elevation decisions and tier order are recorded in `VARYN.md`.

## Live Demo

- **Frontend (HUD):** https://varyn-ai.vercel.app
- **Backend health check:** https://varyn.onrender.com/health

The backend runs on Render's free tier and is kept warm by a cron job hitting a lightweight `/ping` endpoint every 5 minutes. If you hit it after a long idle period, allow a few seconds for a cold start.

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

Configure OpenRouter and FRED without committing the local `.env` file:

```text
OPENROUTER_API_KEY=
OPENROUTER_MODEL=openai/gpt-oss-20b:free
OPENROUTER_FALLBACK_MODEL=openrouter/free
FRED_API_KEY=
```

The Next.js proxy reads `VARYN_AGENT_URL` from `C:\varyn\.env.local`. The local default is:

```text
VARYN_AGENT_URL=http://127.0.0.1:8788
```

## Deployment

| Service | Platform | Notes |
|---|---|---|
| Backend | Render.com | Root directory `agent`; Python pinned to 3.11 via `runtime.txt`/`.python-version`; numpy pinned to 1.26.4; auto-deploys on changes inside `/agent` |
| Frontend | Vercel | Production domain `varyn-ai.vercel.app`; auto-deploys on any push to `main` |
| Durable facts / rate limits | Upstash Redis | Stores hosted long-term facts and anonymous demo quota counters through REST credentials |
| Keep-alive | cron-job.org | Hits `GET /ping` every 5 minutes to prevent Render's free-tier 15-minute spin-down |

Render supplies `PORT` automatically. `FRONTEND_URL` and `PYTHON_VERSION` are supplied via `render.yaml`. Vercel points `VARYN_AGENT_URL` at the Render backend's HTTPS URL and stores the proxy/auth/rate-limit variables. Render stores the matching `VARYN_PROXY_SECRET` plus `KV_REST_API_URL` and `KV_REST_API_TOKEN` for hosted durable facts. Provider API keys are never stored on the frontend.

## Automated Reliability Tests

Varyn now includes a persistent automated reliability suite covering the safety-critical logic that
previously required manual verification. The current suite contains 217 tests: 171 backend pytest
tests and 46 frontend Vitest tests.

Coverage includes one-time confirmation enforcement, session and uploaded-file isolation,
prompt-injection detection, upload size/type restrictions, owner bypass and rate-limit regression
logic, hosted persistence backend selection, audit rotation, risk memo evidence validation, source
confidence/provenance normalization, provider failover/timeout behavior, risk-score regression
checks, and speech normalization. The suite uses mocks and temporary directories only; it does not
hit live OpenRouter, Upstash, Vercel, Render, or other external services.

Development checks:

```powershell
cd C:\varyn
python -m pytest agent/tests/ -q
npm run test
npm run build
npm run lint
```

This testing layer is intentionally reliability infrastructure rather than a new product feature:
it helps future development move faster without quietly weakening Varyn's security, persistence,
risk-analysis, or voice behavior.

## Roadmap

- [ ] Optional Render-side secondary rate limiting behind the proxy-secret gate for additional defense in depth
- [ ] Bank/financial-institution-specific fundamentals mapping (deposits, loan-to-deposit ratio, Tier 1 capital, net interest margin)
- [ ] Optional news/sentiment data layer
- [ ] Optional extension of the compliance layer using Federal Reserve enforcement-action data
- [ ] Spoken-date formatting for voice output (ISO dates → natural speech)
- [ ] Private differentiator, built separately with security and IP protection

## Limitations

- Market, credit, and compliance-signal analysis are preliminary, not financial, credit, or legal advice
- Free-tier LLM reasoning can be slower or more variable than paid models
- Bank fundamentals are not yet fully mapped (see [Roadmap](#roadmap))
- Render's free tier caps available memory at 512MB; the keep-alive job mitigates cold starts but doesn't eliminate all latency
- Hosted demo session state, uploads, caches, heartbeat state, and temporary runtime files are intentionally ephemeral; explicit long-term facts persist through Upstash Redis

## Why I Built This

I'm a Finance and Data Science student with hands-on experience in banking, operational reporting automation, and risk management. Varyn started as a way to prove I could build a serious AI system end to end, not just prompt an API, but design a tool-calling agent, validate data across multiple official sources, add real safety rails, and ship something live. Its latest stabilization work hardened the public backend, clarified hosted persistence, improved mobile usability, and added automated reliability tests, moving Varyn from a memorable MVP toward a more disciplined software system. It's the technical foundation and proof-of-work behind my longer-term interest in operational risk intelligence for organizations that don't have the infrastructure of a large financial institution.

## About

**Abubakr Jallow**
Finance & Data Science, Canisius College
[LinkedIn](https://www.linkedin.com/in/abubakr1/) · [GitHub](https://github.com/abujallow/varyn)

