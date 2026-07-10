# CLAUDE.md — Varyn Handoff

Context primer for a new Claude session picking up work on Varyn. Read this first;
it links to deeper docs where needed. Nothing here is a secret — actual credentials
live only in untracked `.env` files and hosting-provider dashboards.

## What Varyn Is

Varyn is a live AI risk intelligence command system: a voice-capable, JARVIS-style
HUD that turns public market/fundamental/macro/regulatory data into source-backed
risk analysis. Built and owned by Abubakr Jallow (college portfolio flagship, not a
production financial system — every analysis is labeled preliminary).

Full build history lives in the "Varyn Project Documentation" doc (kept outside this
repo, in the owner's Downloads as PDF/DOCX) — six parts covering prototype → hardened
agent → going live → stabilization → trust refinement → market-watch recovery. This
file is the condensed, code-facing version of that history.

## Architecture

- **Frontend**: Next.js (App Router), single HUD in `src/app/page.js` (~2,050 lines,
  monolithic — see "Known Issues" below). Voice via Web Speech API, pure helpers
  extracted to `src/app/speech.js` and `src/app/marketTicker.js`.
- **Backend**: Python FastAPI in `agent/`, entry point `agent/main.py`. Tool-calling
  agent core in `agent/agent_core.py`, tools in `agent/tools/`.
- **Frontend deploy**: Vercel, production domain `https://varyn-ai.vercel.app`.
  Manual `vercel deploy --prod` (not confirmed to auto-deploy from git push — verify
  before assuming).
- **Backend deploy**: Render, `https://varyn.onrender.com`. Auto-deploys on changes
  under `/agent` per `render.yaml`; kept warm by a 5-minute `/ping` cron job.
- **AI provider**: OpenRouter, primary model `openai/gpt-oss-20b:free`, with a
  multi-model free fallback chain (`agent/providers.py`). Gemini stays optional,
  never required.
- **Data sources**: yfinance (primary market data, fragile — scrapes Yahoo, breaks
  periodically), Stooq (secondary/cross-check, often blocked on Render's IP — this
  is expected, not a bug), SEC EDGAR (official fundamentals), FRED (macro), CFPB
  (regulatory/compliance signal, keyless, on-demand only).
- **Persistence**: Local dev uses file-backed storage under `agent/data/` (gitignored).
  Hosted (Render) uses Upstash Redis **only** for durable long-term remembered facts
  (`agent/memory.py`); everything else hosted (sessions, uploads, caches, heartbeat
  state) is intentionally ephemeral — this is a deliberate privacy/scope choice, not
  a gap. See `agent/memory.py`'s `_UpstashBackend` vs `_LocalFileBackend`.

## Security (Priority 1 — complete)

- `agent/security.py` is global FastAPI middleware. Every route except
  `PUBLIC_PATHS = {"/ping", "/health"}` requires header `X-Varyn-Proxy-Key` to match
  `VARYN_PROXY_SECRET` (shared secret between Vercel and Render), or it 401s.
- Vercel (`src/lib/varyn-security.js`) is the only party that knows the proxy secret
  and attaches it server-side — the browser never sees it.
- Owner role: `X-Varyn-Role: owner` header, only set after Vercel verifies an
  HMAC-signed owner cookie (`authenticateOwner`/`isOwnerRequest` in
  `varyn-security.js`). The owner access key is hash-compared (`VARYN_OWNER_ACCESS_HASH`,
  SHA-256) — plaintext is never stored server-side and Claude does not have it.
- `OWNER_PREFIXES` in `security.py` gates `/audit`, `/safety`, `/confirmations/`,
  `/upload`, `/files/`, `/session/`, plus `/heartbeat/run`, `/heartbeat/notices/*`,
  and `/sec/fundamentals/*` + `/cfpb/*` only when `?refresh=true`.
- Rate limiting (`enforceChatLimit` in `varyn-security.js`, Upstash `@upstash/ratelimit`):
  public users capped at **10 requests/hour per IP AND per session**, plus 25/day and
  800/day global backstops. Owner role bypasses this entirely
  (`if (isOwner) return { blocked: false }`).
- Owner login itself is separately rate-limited (`enforceOwnerLoginLimit`) to resist
  brute-forcing the access key.
- `/health` is sanitized; full diagnostics live behind owner-gated `/health/details`.

**Preserve this exactly.** Do not weaken proxy-secret enforcement, do not make owner
routes reachable by demo role, do not raise/remove the public rate limit without being
asked, never log or print the proxy secret / auth secret / owner access key or hash.

## Test Suite

**171 pytest tests** (`agent/tests/`) + **46 Vitest tests** (`src/**/__tests__/`).
All network calls (OpenRouter, Gemini, yfinance company search, Upstash, Vercel, Render)
are mocked — the suite must never make live external calls.

Run:
```bash
# Backend (from agent/)
python -m pytest tests/ -q

# Frontend (from repo root)
npm run test
npm run lint
npm run build
```

Per-file backend counts: `test_risk_routing.py` (28), `test_risk_memo.py` (18),
`test_providers_http.py` (60 — HTTP/retry/fallback/streaming layer, added Mini Update 1),
`test_providers.py` (14 — pure helper math), `test_memory.py` (10), `test_safety.py` (10),
`test_risk.py` (9), `test_heartbeat_market_snapshot.py` (9), `test_security.py` (6),
`test_files.py` (4), `test_audit.py` (3).

## Recent Fixes (most recent first)

**Mini Update 1 — provider HTTP-layer test coverage** — Added `agent/tests/test_providers_http.py`
(60 tests) covering the previously-untested execution path in `agent/providers.py`:
`post_json`/`call_openrouter` (success, `HTTPError`, `URLError`, `TimeoutError`),
`call_openrouter_stream` (SSE token/tool-call delta parsing, malformed lines,
mid-stream errors), `parse_openrouter_response`, `parse_native_tool_calls`,
`parse_structured_actions`, `parse_tagged_tool_calls`, `validated_model_chain`
(free-suffix + catalog filtering), `call_gemini`, and the full `complete()` /
`stream_complete()` retry-and-fallback orchestration (transient vs. non-transient
failures, retry-budget exhaustion, chain-to-Gemini, chain-to-local-offline, the
streaming "interrupted after partial tokens" path). All `urllib.request` calls are
mocked; no production code changed. No production defect was found — every existing
behavior held under test. Dedicated `SecretRedactionTests` confirm the API key never
appears in `ProviderResult.error`, replies, or the audit log across the real
`call_openrouter`/`post_json` exception-building path.

**Market-watch/heartbeat regression** (commit `28a0bc0`) — The single-entity scoring
gate below (correctly) started returning `overall_score: None` for the heartbeat's
lightweight market snapshot (which only has price/change_percent, never
beta/debt-to-equity/current-ratio). `evaluate_watched_symbol()` in `agent/heartbeat.py`
did an unguarded `int(values["risk_score"])`, crashed on the first watchlist symbol
every cycle, and the exception was swallowed before `state["last_values"]` could
update — freezing the MARKET WATCH ticker row at "Unavailable" forever, even though
DATA HEALTH showed yfinance as active (that health check runs earlier in the same
tick, before the crash). **Fix**: added `heartbeat_risk_score()` which calls the
*ungated* `score_from_context()` directly — the heartbeat only ever needed a cheap
operational number for its own alert thresholds, not a fundamentals-backed memo
score, so it was never meant to be subject to the chat-facing gate. Also made
`evaluate_watched_symbol()` tolerate `None` defensively. **Lesson (keep this in
mind for any future scoring/gating work): chat-facing analytical scoring and
heartbeat/operational scoring are different problems and must stay on separate
code paths.** Extracted ticker formatting to `src/app/marketTicker.js`; fixed a JS
edge case where `Number(null)`/`Number("")` both coerce to `0`, so a genuinely
missing price was rendering as `$0.00` instead of `Unavailable` (`isRealNumber()`
guard added).

**Single-entity risk routing + missing-data-aware scoring** (commit `53eacdd`) — A
real tester asked "What are the biggest current risks for JPMorgan, and what sources
support your answer?" and got a "Multi-Company Risk Comparison" with an overall score
of 46 despite price/beta/debt-to-equity/current-ratio all showing unavailable. Root
cause in `agent/tools/risk.py`'s `build_risk_analysis()`: comparison mode triggered
too loosely (`context_count > 1 or "compare" in message`), and scores were computed
from message keywords without checking whether real data existed. **Fix**:
`has_explicit_comparison_language()` — comparison mode is now opt-in, requiring
explicit language (compare/versus/vs/rank/between/relative to/"which is riskier") or
genuinely multiple named entities; single entities (companies, banks, universities,
agencies, nonprofits — ticker-mapped or not) default to a single-entity memo.
`assess_score_availability()` — refuses a precise `overall_risk_score` unless ≥2 of
4 key fields are real; otherwise returns `score_available: false`,
`overall_risk_score: null`, `score_confidence: "insufficient_data"`, and a
`data_gaps` list. Frontend (`page.js` analysis panel) only renders the numeric score
when `score_available === true`; otherwise shows "Insufficient data to calculate a
reliable score" plus the missing fields.

**Priority 1–3** (public backend protection, hosted persistence, automated tests) —
see Security section above and Test Suite section above; both are load-bearing and
already covered.

## Known Limitations / Intentionally Deferred

- Bank/financial-institution fundamentals aren't mapped — banks return "Not
  available" for generic corporate ratios (deposits, loan-to-deposit, Tier 1 capital,
  NIM would need their own mapping). Main open data gap, understood, not urgent.
- No news/sentiment data layer — deliberately deferred, not accidental.
- Spoken-date formatting and the multi-size favicon export are unfinished/in-progress.
- OCC and Federal Reserve enforcement-action data are documented options only, not built.
- The "private differentiator" stays out of this shared codebase by design — do not
  try to infer or reconstruct it.
- Product scope is intentionally frozen at a single layer (no "Layer 2" comparison
  product) — do not propose expanding scope unless explicitly asked.

## Latest Comprehensive Review Findings (not yet actioned)

- ~~`agent/providers.py` had almost no direct test coverage~~ — **resolved in Mini
  Update 1** (see Recent Fixes above); `call_openrouter`, `call_openrouter_stream`,
  and all response-parsing functions now have dedicated coverage in
  `agent/tests/test_providers_http.py`.
- **`src/app/page.js` is a 2,049-line file, ~1,900-line single component** (32
  `useState`, 27 `useCallback`, 43 `useRef`). No `src/components/` decomposition
  exists. Real technical debt, but only worth splitting incrementally as features are
  touched anyway — not as a standalone refactor project (regression risk).
- **5 of 10 direct npm dependencies appear unused**: `framer-motion`,
  `@emailjs/browser`, `emailjs-com` (abandoned upstream), `react-countup`,
  `react-type-animation`. Safe, low-priority cleanup.
- **`main.py` route handlers lack HTTP-level (`TestClient`) tests** for most routes
  (`/chat`, `/chat/stream`, `/sec/*`, `/fred/*`, `/heartbeat`, `/audit`, etc.) — logic
  underneath is tested directly, but not through the actual FastAPI request/response
  boundary.
- A couple of bare `except Exception:` blocks discard the error object
  (`risk_memo.py:415`, `:801`) — not currently causing bugs, but worth logging
  rather than swallowing.
- **Overall guidance from the review: do not add new features or data sources right
  now.** The project is appropriately developed for its stage. If asked to keep
  improving, prioritize test/reliability depth (`providers.py` coverage) over new
  surface area.

## Rules Future Sessions Must Preserve

1. **Never invent data.** Unavailable fields must be labeled clearly (`"Unavailable
   from free source"` in `tools/risk.py`, `"Not available"` in `risk_memo.py` — these
   two conventions are intentionally slightly different, don't "fix" the mismatch
   without checking both call sites first).
2. **Never fabricate a numeric risk score without sufficient data.** Preserve
   `assess_score_availability()`'s gate on `build_risk_analysis()`. Do not lower the
   `SCORE_MIN_AVAILABLE_FIELDS` threshold or bypass it to "make the UI look fuller."
3. **Single-entity is the default routing outcome.** Comparison mode must stay
   opt-in (explicit language or genuinely multiple named entities) — never make it
   the default or trigger it on loose heuristics again.
4. **Keep chat-facing scoring and heartbeat/operational scoring on separate code
   paths.** Do not route heartbeat's lightweight snapshot back through
   `build_risk_analysis()`.
5. **Confirmation gates are a hard stop.** High-impact actions (remember/update/forget
   fact, export risk memo, session reset) require an explicit user confirmation via
   `agent/safety.py`'s `SafetyRails` — never claim an action ran before the backend
   confirms it executed.
6. **Session and file-context isolation is explicit, not implicit.** Uploaded files
   and session memory must never leak across `session_id` boundaries; `MemoryStore`
   already TTL-prunes stale sessions — don't remove that.
7. **Security controls are not optional in any environment.** Don't add a "dev
   bypass" that could ship to production; `security_required()` already treats
   `RENDER` env presence as force-on.
8. **Graceful degradation, not hard failure.** One failed data source (Stooq being
   blocked, one ticker's fetch failing) must never blank unrelated data — see the
   market-watch fix above for exactly this principle in practice.
9. **No secrets in code, logs, commits, or chat.** Never print `VARYN_PROXY_SECRET`,
   `VARYN_AUTH_SECRET`, `VARYN_OWNER_ACCESS_HASH`, `KV_REST_API_TOKEN`, or
   `OPENROUTER_API_KEY` — verify presence/length only, as established since the
   project's very first API-key-exposure incident.
10. **Tests must never hit live external services.** Mock OpenRouter, yfinance,
    Upstash, Vercel, Render — always.

## Where to Look for More

- `README.md` — full public-facing architecture doc (404 lines), install/deploy
  instructions, test suite section. Read this for anything not covered here.
- `VARYN.md` — living internal description of system/architecture/current state.
- `VARYN-ROADMAP.md` — ordered tier/phase plan with status.
- `agent/security.py`, `src/lib/varyn-security.js` — source of truth for auth/rate-limiting.
- `agent/tools/risk.py` — routing + scoring logic (heavily revised recently, read
  before touching).
- `agent/heartbeat.py` — watchlist monitoring; note `heartbeat_risk_score()` vs
  `build_risk_analysis()` split.
- `agent/tests/`, `src/**/__tests__/` — read existing tests before adding new ones,
  to match established mocking patterns (temp dirs, no live network).
