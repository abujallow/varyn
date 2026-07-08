import { createHash, createHmac, timingSafeEqual } from "node:crypto";
import { cookies } from "next/headers";
import { Ratelimit } from "@upstash/ratelimit";
import { Redis } from "@upstash/redis";

const OWNER_COOKIE = "varyn_owner";
const OWNER_SESSION_SECONDS = Number(process.env.VARYN_OWNER_SESSION_SECONDS || 60 * 60 * 8);
const encoder = new TextEncoder();

let redis;
let limiters;

function securitySecret() {
  return process.env.VARYN_AUTH_SECRET || "";
}

function proxySecret() {
  return process.env.VARYN_PROXY_SECRET || "";
}

function ownerAccessHash() {
  return (process.env.VARYN_OWNER_ACCESS_HASH || "").trim().toLowerCase();
}

function safeEqual(left, right) {
  const a = Buffer.from(left || "");
  const b = Buffer.from(right || "");
  return a.length === b.length && timingSafeEqual(a, b);
}

function sign(value) {
  return createHmac("sha256", securitySecret()).update(value).digest("base64url");
}

function makeOwnerToken() {
  const payload = Buffer.from(
    JSON.stringify({ role: "owner", exp: Math.floor(Date.now() / 1000) + OWNER_SESSION_SECONDS }),
  ).toString("base64url");
  return `${payload}.${sign(payload)}`;
}

function verifyOwnerToken(token) {
  if (!token || !securitySecret()) return false;
  const [payload, signature] = token.split(".");
  if (!payload || !signature || !safeEqual(signature, sign(payload))) return false;
  try {
    const parsed = JSON.parse(Buffer.from(payload, "base64url").toString("utf8"));
    return parsed.role === "owner" && Number(parsed.exp) > Math.floor(Date.now() / 1000);
  } catch {
    return false;
  }
}

export function ownerAuthConfigured() {
  return Boolean(securitySecret() && ownerAccessHash());
}

export async function isOwnerRequest() {
  const store = await cookies();
  return verifyOwnerToken(store.get(OWNER_COOKIE)?.value);
}

export async function authenticateOwner(accessKey) {
  if (!ownerAuthConfigured() || typeof accessKey !== "string") return false;
  const candidate = createHash("sha256").update(accessKey).digest("hex");
  if (!safeEqual(candidate, ownerAccessHash())) return false;

  const store = await cookies();
  store.set(OWNER_COOKIE, makeOwnerToken(), {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "strict",
    maxAge: OWNER_SESSION_SECONDS,
    path: "/",
  });
  return true;
}

export async function clearOwnerSession() {
  const store = await cookies();
  store.set(OWNER_COOKIE, "", {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "strict",
    expires: new Date(0),
    path: "/",
  });
}

export function ownerRequiredResponse() {
  return Response.json(
    { error: "Owner authentication is required for this action.", code: "OWNER_REQUIRED" },
    { status: 403 },
  );
}

export function securityUnavailableResponse() {
  return Response.json(
    { error: "Varyn security configuration is unavailable. This request was blocked safely." },
    { status: 503 },
  );
}

export function agentHeaders(role = "demo", extra = {}) {
  const secret = proxySecret();
  return {
    ...extra,
    ...(secret ? { "X-Varyn-Proxy-Key": secret } : {}),
    "X-Varyn-Role": role === "owner" ? "owner" : "demo",
  };
}

export function securityReadyForProduction() {
  return process.env.NODE_ENV !== "production" || Boolean(proxySecret());
}

export function scopedSessionId(req, rawSessionId) {
  const forwarded = req.headers.get("x-forwarded-for")?.split(",")[0]?.trim();
  const address = forwarded || req.headers.get("x-real-ip") || "local";
  const raw = `${address}:${String(rawSessionId || "browser-session").slice(0, 120)}`;
  return `session-${createHmac("sha256", securitySecret() || proxySecret() || "varyn-local")
    .update(raw)
    .digest("hex")
    .slice(0, 32)}`;
}

function redisClient() {
  if (redis) return redis;
  const url = process.env.KV_REST_API_URL;
  const token = process.env.KV_REST_API_TOKEN;
  if (!url || !token) return null;
  redis = new Redis({ url, token });
  return redis;
}

function configuredLimiters() {
  if (limiters) return limiters;
  const client = redisClient();
  if (!client) return null;
  limiters = {
    chatHour: new Ratelimit({
      redis: client,
      limiter: Ratelimit.slidingWindow(Number(process.env.VARYN_CHAT_HOURLY_LIMIT || 10), "1 h"),
      prefix: "varyn:chat:hour",
      analytics: false,
    }),
    chatDay: new Ratelimit({
      redis: client,
      limiter: Ratelimit.fixedWindow(Number(process.env.VARYN_CHAT_DAILY_LIMIT || 25), "1 d"),
      prefix: "varyn:chat:day",
      analytics: false,
    }),
    chatSessionHour: new Ratelimit({
      redis: client,
      limiter: Ratelimit.slidingWindow(Number(process.env.VARYN_CHAT_HOURLY_LIMIT || 10), "1 h"),
      prefix: "varyn:chat:session-hour",
      analytics: false,
    }),
    chatSessionDay: new Ratelimit({
      redis: client,
      limiter: Ratelimit.fixedWindow(Number(process.env.VARYN_CHAT_DAILY_LIMIT || 25), "1 d"),
      prefix: "varyn:chat:session-day",
      analytics: false,
    }),
    globalDay: new Ratelimit({
      redis: client,
      limiter: Ratelimit.fixedWindow(Number(process.env.VARYN_GLOBAL_DAILY_LIMIT || 800), "1 d"),
      prefix: "varyn:chat:global",
      analytics: false,
    }),
    ownerLogin: new Ratelimit({
      redis: client,
      limiter: Ratelimit.slidingWindow(Number(process.env.VARYN_OWNER_LOGIN_LIMIT || 5), "15 m"),
      prefix: "varyn:owner-login",
      analytics: false,
    }),
  };
  return limiters;
}

function anonymousIdentifier(req, sessionId) {
  const forwarded = req.headers.get("x-forwarded-for")?.split(",")[0]?.trim();
  const address = forwarded || req.headers.get("x-real-ip") || "unknown";
  const stableAddress = address === "unknown" ? `unknown:${sessionId || "browser-session"}` : address;
  return createHash("sha256").update(stableAddress).digest("hex");
}

function anonymousSessionIdentifier(sessionId) {
  return createHash("sha256")
    .update(String(sessionId || "browser-session").slice(0, 120))
    .digest("hex");
}

export async function enforceChatLimit(req, sessionId, isOwner) {
  if (isOwner) return { blocked: false };
  const active = configuredLimiters();
  if (!active) {
    if (process.env.NODE_ENV === "production") {
      return {
        blocked: true,
        response: Response.json(
          { error: "Anonymous request protection is temporarily unavailable. Please try again later." },
          { status: 503 },
        ),
      };
    }
    return { blocked: false };
  }

  const identifier = anonymousIdentifier(req, sessionId);
  const sessionIdentifier = anonymousSessionIdentifier(sessionId);
  try {
    const [hour, day, sessionHour, sessionDay, global] = await Promise.all([
      active.chatHour.limit(identifier),
      active.chatDay.limit(identifier),
      active.chatSessionHour.limit(sessionIdentifier),
      active.chatSessionDay.limit(sessionIdentifier),
      active.globalDay.limit("all-anonymous-users"),
    ]);
    const results = { hour, day, sessionHour, sessionDay, global };
    const denied = Object.values(results).find((result) => !result.success);
    const diagnosticHeaders = Object.fromEntries(
      Object.entries(results).flatMap(([name, result]) => [
        [`X-RateLimit-${name}-Limit`, String(result.limit)],
        [`X-RateLimit-${name}-Remaining`, String(result.remaining)],
        [`X-RateLimit-${name}-Reset`, String(result.reset)],
      ]),
    );
    if (!denied) return { blocked: false, headers: diagnosticHeaders };
    return {
      blocked: true,
      headers: diagnosticHeaders,
      response: Response.json(
        {
          error: "The anonymous Varyn demo limit has been reached. Please try again after the limit resets.",
          code: "RATE_LIMITED",
        },
        {
          status: 429,
          headers: {
            ...diagnosticHeaders,
            "Retry-After": String(Math.max(1, Math.ceil((denied.reset - Date.now()) / 1000))),
            "X-RateLimit-Limit": String(denied.limit),
            "X-RateLimit-Remaining": String(denied.remaining),
            "Cache-Control": "no-store",
          },
        },
      ),
    };
  } catch {
    return {
      blocked: true,
      response: Response.json(
        { error: "Anonymous request protection is temporarily unavailable. Please try again later." },
        { status: 503 },
      ),
    };
  }
}

export async function enforceOwnerLoginLimit(req) {
  const active = configuredLimiters();
  if (!active) {
    return process.env.NODE_ENV === "production"
      ? Response.json({ error: "Owner authentication is temporarily unavailable." }, { status: 503 })
      : null;
  }
  try {
    const result = await active.ownerLogin.limit(anonymousIdentifier(req));
    if (result.success) return null;
    return Response.json(
      { error: "Too many owner-access attempts. Please wait before trying again." },
      {
        status: 429,
        headers: { "Retry-After": String(Math.max(1, Math.ceil((result.reset - Date.now()) / 1000))) },
      },
    );
  } catch {
    return Response.json({ error: "Owner authentication is temporarily unavailable." }, { status: 503 });
  }
}
