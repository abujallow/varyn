const DEFAULT_AGENT_URL = "http://127.0.0.1:8788";

export async function GET() {
  return proxyHeartbeat("/heartbeat", { method: "GET" });
}

export async function POST(req) {
  const body = await req.json().catch(() => ({}));
  if (body.action === "dismiss" && body.noticeId) {
    return proxyHeartbeat(`/heartbeat/notices/${encodeURIComponent(body.noticeId)}/dismiss`, {
      method: "POST",
    });
  }
  if (body.action === "run") {
    return proxyHeartbeat("/heartbeat/run", { method: "POST" });
  }
  return Response.json({ error: "Unknown heartbeat action." }, { status: 400 });
}

async function proxyHeartbeat(path, options) {
  try {
    const agentUrl = process.env.VARYN_AGENT_URL || DEFAULT_AGENT_URL;
    const response = await fetch(`${agentUrl}${path}`, {
      ...options,
      cache: "no-store",
      signal: AbortSignal.timeout(5000),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      return Response.json(
        { error: data.detail || data.error || "Heartbeat request failed." },
        { status: response.status },
      );
    }
    return Response.json(data, { headers: { "Cache-Control": "no-store" } });
  } catch (error) {
    console.error("Varyn heartbeat proxy error:", error);
    return Response.json(
      { error: "The local Varyn heartbeat is unavailable." },
      { status: 503 },
    );
  }
}
