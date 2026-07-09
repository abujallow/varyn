import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

function makeRequest(headers = {}) {
  const map = new Map(Object.entries(headers));
  return { headers: { get: (key) => map.get(key.toLowerCase()) ?? map.get(key) ?? null } };
}

function makeCookieStore() {
  const store = new Map();
  return {
    get: (name) => (store.has(name) ? { value: store.get(name) } : undefined),
    set: (name, value) => store.set(name, value),
  };
}

let cookieStore;

vi.mock("next/headers", () => ({
  cookies: async () => cookieStore,
}));

vi.mock("@upstash/redis", () => ({
  Redis: class {},
}));

const limiterInstances = {};

function makeMockLimiter(name) {
  const instance = { limit: vi.fn().mockResolvedValue({ success: true, limit: 10, remaining: 9, reset: Date.now() + 1000 }) };
  limiterInstances[name] = instance;
  return instance;
}

vi.mock("@upstash/ratelimit", () => {
  class Ratelimit {
    constructor({ prefix }) {
      return limiterInstances[prefix] || makeMockLimiter(prefix);
    }
    static slidingWindow() {
      return { type: "sliding" };
    }
    static fixedWindow() {
      return { type: "fixed" };
    }
  }
  return { Ratelimit };
});

async function freshModule(env = {}) {
  vi.resetModules();
  for (const key of Object.keys(limiterInstances)) delete limiterInstances[key];
  const prevEnv = { ...process.env };
  Object.assign(process.env, env);
  const mod = await import("../varyn-security.js");
  return { mod, restore: () => (process.env = prevEnv) };
}

beforeEach(() => {
  cookieStore = makeCookieStore();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("owner rate-limit bypass", () => {
  it("bypasses the limiter entirely when isOwner is true, without calling any limiter", async () => {
    const { mod, restore } = await freshModule({
      KV_REST_API_URL: "https://example.invalid",
      KV_REST_API_TOKEN: "token",
      NODE_ENV: "production",
    });
    const result = await mod.enforceChatLimit(makeRequest(), "session-1", true);
    expect(result.blocked).toBe(false);
    expect(Object.values(limiterInstances).some((l) => l.limit.mock.calls.length > 0)).toBe(false);
    restore();
  });

  it("does not bypass for a non-owner request", async () => {
    const { mod, restore } = await freshModule({
      KV_REST_API_URL: "https://example.invalid",
      KV_REST_API_TOKEN: "token",
      NODE_ENV: "production",
    });
    const result = await mod.enforceChatLimit(makeRequest(), "session-1", false);
    expect(result.blocked).toBe(false); // mock limiters all succeed
    expect(Object.values(limiterInstances).some((l) => l.limit.mock.calls.length > 0)).toBe(true);
    restore();
  });
});

describe("rate limit denial", () => {
  it("returns 429 when any limiter denies", async () => {
    const { mod, restore } = await freshModule({
      KV_REST_API_URL: "https://example.invalid",
      KV_REST_API_TOKEN: "token",
      NODE_ENV: "production",
    });
    // Trigger construction, then force one limiter to deny.
    await mod.enforceChatLimit(makeRequest(), "warm-up", false);
    limiterInstances["varyn:chat:hour"].limit.mockResolvedValueOnce({
      success: false,
      limit: 10,
      remaining: 0,
      reset: Date.now() + 60000,
    });
    const result = await mod.enforceChatLimit(makeRequest(), "session-2", false);
    expect(result.blocked).toBe(true);
    expect(result.response.status).toBe(429);
    restore();
  });
});

describe("redis unavailable branching", () => {
  it("blocks with 503 in production when Redis env vars are missing", async () => {
    const { mod, restore } = await freshModule({ NODE_ENV: "production" });
    delete process.env.KV_REST_API_URL;
    delete process.env.KV_REST_API_TOKEN;
    const result = await mod.enforceChatLimit(makeRequest(), "session-3", false);
    expect(result.blocked).toBe(true);
    expect(result.response.status).toBe(503);
    restore();
  });

  it("allows through in development when Redis env vars are missing", async () => {
    const { mod, restore } = await freshModule({ NODE_ENV: "development" });
    delete process.env.KV_REST_API_URL;
    delete process.env.KV_REST_API_TOKEN;
    const result = await mod.enforceChatLimit(makeRequest(), "session-4", false);
    expect(result.blocked).toBe(false);
    restore();
  });
});

describe("owner authentication", () => {
  it("accepts the correct access key and sets a session cookie", async () => {
    const { mod, restore } = await freshModule({
      VARYN_AUTH_SECRET: "test-auth-secret",
      VARYN_OWNER_ACCESS_HASH:
        // sha256("correct-key")
        "ddb0fd2dede48502669718e09ef1447dba46f3d3822e9fbf05af11d874a0f23b",
    });
    const ok = await mod.authenticateOwner("correct-key");
    expect(ok).toBe(true);
    const isOwner = await mod.isOwnerRequest();
    expect(isOwner).toBe(true);
    restore();
  });

  it("rejects an incorrect access key", async () => {
    const { mod, restore } = await freshModule({
      VARYN_AUTH_SECRET: "test-auth-secret",
      VARYN_OWNER_ACCESS_HASH:
        "ddb0fd2dede48502669718e09ef1447dba46f3d3822e9fbf05af11d874a0f23b",
    });
    const ok = await mod.authenticateOwner("wrong-key");
    expect(ok).toBe(false);
    const isOwner = await mod.isOwnerRequest();
    expect(isOwner).toBe(false);
    restore();
  });

  it("rejects a tampered owner cookie", async () => {
    const { mod, restore } = await freshModule({
      VARYN_AUTH_SECRET: "test-auth-secret",
      VARYN_OWNER_ACCESS_HASH:
        "ddb0fd2dede48502669718e09ef1447dba46f3d3822e9fbf05af11d874a0f23b",
    });
    await mod.authenticateOwner("correct-key");
    cookieStore.set("varyn_owner", "tampered.token-value");
    const isOwner = await mod.isOwnerRequest();
    expect(isOwner).toBe(false);
    restore();
  });
});

describe("identifier hashing", () => {
  it("is deterministic for the same IP and session", async () => {
    const { mod, restore } = await freshModule({ VARYN_AUTH_SECRET: "secret" });
    const req = makeRequest({ "x-forwarded-for": "203.0.113.5" });
    const a = mod.scopedSessionId(req, "session-x");
    const b = mod.scopedSessionId(req, "session-x");
    expect(a).toBe(b);
    restore();
  });

  it("differs for different sessions from the same IP", async () => {
    const { mod, restore } = await freshModule({ VARYN_AUTH_SECRET: "secret" });
    const req = makeRequest({ "x-forwarded-for": "203.0.113.5" });
    const a = mod.scopedSessionId(req, "session-x");
    const b = mod.scopedSessionId(req, "session-y");
    expect(a).not.toBe(b);
    restore();
  });
});
