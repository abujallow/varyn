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

- **Frontend**: Next.js (App Router), single HUD orchestrated from `src/app/page.js`
  (~1,830 lines — still the owner of all state/effects/refs/API calls, see "Known
  Issues" below). Voice via Web Speech API, pure helpers extracted to
  `src/app/speech.js`, `src/app/marketTicker.js`, and `src/app/systemHealth.js`.
  Four presentation-only components extracted to `src/components/` (Mini Update 2):
  `OrbitalField`, `MarketTicker`, `SystemPanel`, `AnalysisPanel` — all receive props
  only, own no state, and are rendered by `Home` in `page.js`.
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
- `OWNER_PREFIXES` in `security.py` gates `/audit`, `/safety`, `/upload`, `/files/`,
  `/session/`, `/health/details` (added Mini Update 4), plus `/heartbeat/run`,
  `/heartbeat/notices/*`, and `/sec/fundamentals/*` + `/cfpb/*` only when
  `?refresh=true`. **`/confirmations/{id}` is intentionally NOT in this list** (see
  "Exportable Risk Memo restoration" in Recent Fixes) — it does its own
  per-confirmation, action-aware owner check in `main.py`'s `resolve_confirmation()`
  (`confirmation_requires_owner()`), since some confirmation-gated actions
  (`export_risk_memo`) are resolvable by any authenticated demo/public session while
  others (`remember_fact`, `update_fact`, `forget_fact`, and any operation-kind
  action like `clear_file_context`/`reset_session`) must stay owner-only. The
  authorization source of truth for tool-level gating is
  `ToolRegistry.is_owner_only(name)` (`tools/registry.py`) — it checks both the
  per-tool `owner_only` flag AND `varyn.config.json`'s `security.owner_only_tools`
  list (both existed and had to be updated together; a tool being un-gated in only
  one of the two is a real bug — see the restoration writeup).
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

**220 pytest tests** (`agent/tests/`) + **72 Vitest tests** (`src/**/__tests__/`).
All network calls (OpenRouter, Gemini, yfinance company search, Upstash, Vercel, Render)
are mocked — the suite must never make live external calls. New backend test files must
pass `audit=` explicitly to every `SafetyRails(...)` and `ToolRuntime(...)` they
construct (see `make_isolated_rails()` pattern in `test_export_risk_memo_flow.py`) --
`SafetyRails.request_confirmation()`/`resolve_confirmation()` and `risk_memo.export_risk_memo()`
all call `audit.log(...)` unconditionally when given one, falling back to the real
`get_audit_logger()` singleton when not. This was the exact bug that caused the
pre-existing `test_safety.py`/`test_heartbeat_market_snapshot.py` audit-log leaks noted
in earlier Mini Updates (now fixed) — and was re-introduced twice while writing the
export-flow restoration tests before being caught and fixed.

Run:
```bash
# Backend (from agent/)
python -m pytest tests/ -q

# Frontend (from repo root)
npm run test
npm run lint
npm run build
```

Per-file backend counts: `test_risk_routing.py` (28), `test_risk_memo.py` (22),
`test_providers_http.py` (60 — HTTP/retry/fallback/streaming layer, added Mini Update 1),
`test_export_risk_memo_flow.py` (18 — new, Exportable Risk Memo restoration),
`test_main_routes.py` (18 — +5 for `/confirmations/{id}` HTTP round trips),
`test_providers.py` (14 — pure helper math), `test_memory.py` (10), `test_safety.py`
(12 — +2 for `peek_confirmation()`), `test_security.py` (13 — +1 for the
`/confirmations/` gating change), `test_risk.py` (9), `test_heartbeat_market_snapshot.py`
(9), `test_files.py` (4), `test_audit.py` (3). Frontend `speech.test.js` (27).

~~Known pre-existing test-isolation gap: `test_safety.py`'s `make_rails()` helper
constructed `SafetyRails` without an injected `audit=`~~ — **fixed** while building the
Exportable Risk Memo restoration tests (`make_rails()` now takes an isolated
`AuditLogger`, required for those new tests to be clean anyway).

## Recent Fixes (most recent first)

**Confirmation-modal double-approval UX fix** — After the Exportable Risk Memo
restoration above, the confirmation modal stayed mounted for the entire memo
generation window (`/confirmations/{id}` is a single blocking backend call — for
`export_risk_memo` that includes real data fetches, an LLM narrative call, and PDF
rendering, 10–30s), with no guard against a second click. A second "Approve once"
click sent a second resolve request for the same confirmation, which the backend
correctly (and unavoidably, given one-time-use semantics) rejected with `"This
confirmation has already been resolved."` **Fix, frontend-only:** new
`src/app/confirmationResolution.js` exports `createSingleFlightGuard()` — a
synchronous-at-call-time guard (not React state, so it can't be bypassed by two
clicks landing in the same render batch) wrapping `resolveConfirmation()` in
`page.js`. On click: `flushSync()` forces an immediate, guaranteed DOM commit of a
new `resolvingDecision` state (both buttons disabled, the clicked one shows
"Approving…"/"Denying…") *before* the fetch is dispatched; the confirmation modal is
then dismissed optimistically, in the same synchronous block, right after the fetch
is dispatched — not after it resolves — so it never lingers during generation.
Response handling (reply, artifacts, activity log) is unchanged and continues in the
background. A network-level failure (`fetch` itself rejects — the request never
reached the server) restores the modal for a clean retry; a backend-returned failure
(expired/reused/wrong-session) does not restore it and surfaces the error normally.
No backend files changed; confirmation semantics (session-matching, expiry,
one-time-use, action-aware owner authorization) are untouched.

**Exportable Risk Memo (Tier 7) restoration — public/demo export access** — A live
tester found that requesting and approving a risk memo (e.g. "Give me a risk memo of
M&T Bank" → confirm) failed every time with the HUD showing repeated
`export_risk_memo failed safely` activity entries and a model-paraphrased "I don't have
permission to create or export files in this session" reply.

**Root cause:** `export_risk_memo` had been made owner-only in commit `202008f`
("Add proxy-authenticated backend, rate limiting, and owner auth") as part of a
broader Priority-1 hardening pass that (correctly) restricted durable-memory and
file-reading tools to owner-only, but was applied too broadly to also gate the
memo *export* itself — a capability the project documentation always described as
a publicly demonstrable, confirmation-gated capability, not an owner-only one. The
`owner_only` check ran **before** confirmation creation, in `RegisteredTool.run()`
(`agent/tools/registry.py`), and was enforced through **two independent, redundant
mechanisms** that both had to be found and fixed: the per-tool `owner_only=True` flag
on the tool registration, and a *separately duplicated* `export_risk_memo` entry in
`agent/varyn.config.json`'s `security.owner_only_tools` list (`RegisteredTool.run()`
checks `self.owner_only or self.name in configured_owner_tools` — fixing only one
left the other still blocking every attempt). The backend memo-generation pipeline
itself (Markdown/HTML/PDF, ReportLab, artifact encoding, missing-bank-fundamentals
labeling) was never broken — confirmed by direct reproduction before any code change.

**Fix (narrowest change that restores the documented behavior without weakening any
other control):**
- `agent/tools/registry.py` — removed `owner_only=True` from `export_risk_memo`'s
  registration only; `remember_fact`/`update_fact`/`forget_fact`/`active_file` keep it.
  Added `ToolRegistry.is_owner_only(name)` — the single source of truth for "does this
  tool require owner role," checking both the flag and the config list so the two can
  never drift out of sync again.
- `agent/varyn.config.json` — removed `"export_risk_memo"` from
  `security.owner_only_tools`.
- `agent/security.py` — removed the blanket `/confirmations/` prefix from
  `OWNER_PREFIXES`. Resolving a confirmation is no longer blanket owner-gated at the
  path level.
- `agent/main.py` — `resolve_confirmation()` now does its own per-confirmation,
  action-aware check (`confirmation_requires_owner()`) *before* calling
  `safety.resolve_confirmation()`: operation-kind actions (`clear_file_context`,
  `reset_session`) and any tool-kind action where `is_owner_only()` is true still
  require owner role to resolve; `export_risk_memo` does not. `execute_approved_confirmation()`
  no longer hardcodes `access_role="owner"` for every confirmed execution — it now
  receives and uses the actual verified role from the resolve request (minimum
  privilege: a demo session's approved export runs as `"demo"`, not silently as
  `"owner"`).
- `agent/safety.py` — added `SafetyRails.peek_confirmation(id)`, a read-only lookup
  used to authorize *before* mutating a confirmation's status.
- `src/app/api/varyn/safety/route.js` — the Vercel proxy's `ownerOnly` gate is now
  conditional: `action: "resolve"` is no longer blanket owner-only (the backend makes
  the real decision); `action: "proactive"` (the kill switch) is unchanged and stays
  strictly owner-only.

**Preserved unchanged:** the confirmation gate itself, session-id matching, expiry,
one-time-use enforcement, the proxy-secret requirement on every protected route, chat
rate limits, audit logging, memo generation/evidence/scoring/missing-data-labeling
logic, and every other owner-only capability.

Verified end-to-end against the real local backend (real yfinance/SEC EDGAR/FRED/CFPB
data, real OpenRouter model call) with no proxy secret configured (local dev default,
non-owner role does not block this path) — "Give me a risk memo of M&T Bank" produced
a live confirmation with no owner-authentication error, approval executed the export
exactly once, and the HUD rendered working MD/HTML/PDF download buttons with valid,
non-fabricated content (missing MTB fundamentals correctly labeled "Not available").
28 new/updated backend tests across `test_export_risk_memo_flow.py` (new),
`test_main_routes.py`, `test_safety.py`, and `test_security.py` cover: demo
confirmation creation, no premature execution, exactly-once execution, cross-session/
expired/reused/unknown confirmation rejection, owner-only actions staying blocked for
demo, all three artifact formats with valid fields, and audit-log content never
including memo text or secrets.

**Mini Update 5 — final polish: spoken dates, favicon verification, heartbeat test
isolation** — Three parts:

1. **Favicon: verified complete, no changes made.** `src/app/{favicon.ico, icon.png,
   apple-icon.png}` (16/32/48 ICO, 512×512 PNG, 180×180 PNG) were already correctly
   wired via Next.js App Router's automatic icon convention and confirmed live in a
   browser (`<link rel="icon">`/`<link rel="apple-touch-icon">` all present, all
   assets 200). This was already finalized in an earlier commit
   (`862cd67`, "Finalize Varyn favicon and command system title") — the "unfinished"
   note in this file's Known Limitations was simply stale and has been removed.
2. **Natural spoken dates (`src/app/speech.js`).** `formatSpokenDate()` previously
   produced digit-form output (`"July 9th, 2026"`). Now produces full word form
   (`"July ninth, twenty twenty six"`): a fixed `DAY_ORDINAL_WORDS` lookup (1–31,
   no algorithm needed) replaces the old digit-suffix `spokenOrdinal()` (removed,
   now dead code); a new `yearToWords()` splits 4-digit years into two word-pairs
   (round-hundred years like 2000/1900 fall back to plain cardinal reading — a
   documented simplification, since Varyn's real dates are modern financial/
   regulatory dates, not historical round-century references). Still only the same
   4 existing numeric formats (`YYYY-MM-DD`, `YYYY/MM/DD`, `MM/DD/YYYY`,
   `MM-DD-YYYY`) — no new format support, no general date parser. Also added a
   `(?<!\/)` guard to all three date regexes after finding a real (if narrow) gap:
   a bare, non-markdown URL with a date-like path (e.g.
   `https://sec.gov/2026/07/09/filing`) would previously have had its path
   segment mangled into a spoken date. Speech-only — visible text, backend
   responses, stored messages, citations, and URLs are untouched. 10 new tests in
   `speech.test.js`; one pre-existing assertion updated since it asserted the old
   digit-year output the whole point of this change was to replace.
3. **Heartbeat test audit-log isolation (`agent/tests/test_heartbeat_market_snapshot.py`).**
   `add_condition_notice()` in `heartbeat.py` calls the real `get_audit_logger()`
   singleton directly rather than an injected instance; one test in this file drives
   a risk score past the notice threshold and was appending real entries to the
   local `agent/data/audit/varyn-audit.jsonl`. Fixed with `setUpModule()`/
   `tearDownModule()` patching `heartbeat.get_audit_logger` to a `MagicMock` for the
   whole file — test-only change, no production code touched. Verified: the real
   audit file's size and mtime are now provably unchanged by this file, both run
   alone and as part of the full suite. **Found but not fixed (out of this update's
   scope):** an equivalent issue in `test_safety.py` — see Test Suite section above.

No provider behavior, scoring, security, session isolation, confirmation gates, rate
limits, persistence, or visible/backend response content changed. `page.js` was not
touched.

**Mini Update 4 — HTTP route-boundary tests, exception logging, and a real
owner-gating fix** — Three parts:

1. **`/health/details` owner-gating correction (production fix).** `CLAUDE.md`
   already documented this route as owner-gated, but `security.py`'s
   `OWNER_PREFIXES` never actually included it — any authenticated demo-role caller
   could read it (payload has no secrets, but is more detailed than the sanitized
   `/health`). Fixed by adding `"/health/details"` to `OWNER_PREFIXES` — a one-line,
   narrowest-possible change; response payload and all other routes unchanged.
   Covered by 3 new `TestClient` tests in `test_security.py` (401 no key / 403 demo /
   200 owner) plus 3 new direct unit tests of `is_owner_path()` itself
   (`OwnerPathGatingTests`), independent of the HTTP layer.
2. **HTTP-level route tests** — new `agent/tests/test_main_routes.py` (13 tests)
   covering `/ping`, `/sec/fundamentals/{symbol}` (including the conditional
   `?refresh=true` owner-gating branch), `/audit` response schema, `/heartbeat`
   contract, and the full `/chat` + `/chat/stream` request/response boundary
   (empty-message validation, stop-command short-circuit, successful-turn schema,
   SSE headers and event framing) — all through the real FastAPI `TestClient`
   against `main.app`, with `main.run_agent_turn`/`run_agent_turn_stream`,
   `main.memory`, `main.long_term_memory`, and `main.audit` mocked so no real
   `agent/data/` files are touched.
3. **Safe logging for the two previously-swallowed exceptions in `risk_memo.py`** —
   `generate_narrative()` (line ~415, LLM narrative call) and
   `build_download_artifacts()` (line ~801, per-format base64 encoding) now log
   `risk_memo_narrative_failed` / `risk_memo_artifact_encoding_failed` via the
   existing `get_audit_logger()` before falling back exactly as before. Logged
   fields are limited to company name / format name plus `error_type` — never raw
   exception text, provider content, or memo content. 4 new tests in
   `test_risk_memo.py` verify both the fallback behavior is unchanged and the log
   contents contain no leaked content.

No provider behavior, scoring, evidence standards, session isolation, confirmation
gates, rate limits, or frontend behavior changed. See the Test Suite section above
for a pre-existing (not introduced here) test-isolation note discovered while
verifying this update.

**Mini Update 3 — remove confirmed-unused frontend dependencies** — Verified
repository-wide (source, tests, config, scripts, docs) that `framer-motion`,
`@emailjs/browser`, `emailjs-com`, `react-countup`, and `react-type-animation` had
zero imports/usages anywhere, then removed all five via `npm uninstall` (updates
`package.json` and `package-lock.json` together; 8 packages removed total including
transitive-only deps, 0 vulnerabilities). `package.json` now lists 6 direct
dependencies, all confirmed in use. No source files changed; no behavior, styling,
or functionality change.

**Mini Update 2 — incremental frontend decomposition** — Extracted four presentation-only
components from `src/app/page.js` into `src/components/`: `OrbitalField.jsx` (static
starfield background), `MarketTicker.jsx` (heartbeat market-watch row), `SystemPanel.jsx`
(telemetry/data-health/agent-status left panel), `AnalysisPanel.jsx` (risk-analysis
results panel, including the `score_available` gate). Also extracted the pure
`sourceStatusLabel()`/`sourceHealthTitle()` helpers to a new `src/app/systemHealth.js`
(same pattern as `marketTicker.js`), with 10 new unit tests. All four components receive
props only and own no state/effects/refs — `Home` in `page.js` still owns all
orchestration, API calls, voice state machine, and callbacks; only the JSX was moved,
verbatim, not rewritten. `page.js` went from ~2,050 to ~1,830 lines (~11% reduction).
Voice controls, the right-side activity/upload panel, and the owner-access/confirmation
flows were deliberately left in `page.js` — too tightly coupled to refs and callbacks to
extract as pure presentation without redesigning state. No production defect found; no
behavioral, visual, or API change.

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
- **`src/app/page.js` is still a ~1,830-line single component** (32 `useState`, 27
  `useCallback`, 43 `useRef` — unchanged by Mini Update 2, which only moved
  presentation JSX out, not state). Four presentation components now live in
  `src/components/` (see Recent Fixes above), but voice controls, the activity/upload
  panel, and owner/confirmation flows are still inline and tightly coupled. Continue
  splitting incrementally as features are touched — not as a standalone refactor
  project (regression risk).
- ~~5 of 10 direct npm dependencies appear unused~~ — **resolved in Mini Update 3**
  (see Recent Fixes above); `framer-motion`, `@emailjs/browser`, `emailjs-com`,
  `react-countup`, and `react-type-animation` were removed after confirming zero
  usage repository-wide.
- ~~`main.py` route handlers lack HTTP-level (`TestClient`) tests~~ — **partially
  resolved in Mini Update 4** (see Recent Fixes above); `/ping`, `/sec/fundamentals`,
  `/audit`, `/heartbeat`, `/chat`, `/chat/stream`, and `/health/details` now have
  bounded `TestClient` coverage in `agent/tests/test_main_routes.py`. `/fred/*` and
  `/cfpb/{symbol}` were deliberately skipped (same conditional owner-gating shape as
  `/sec/fundamentals/`, already proven) — still open if broader coverage is wanted.
- ~~A couple of bare `except Exception:` blocks discard the error object
  (`risk_memo.py:415`, `:801`)~~ — **resolved in Mini Update 4** (see Recent Fixes
  above); both now log via `get_audit_logger()` with no content/secrets exposed.
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
- `src/components/` — presentation-only HUD components extracted from `page.js`
  (Mini Update 2); each takes props only and owns no state.
