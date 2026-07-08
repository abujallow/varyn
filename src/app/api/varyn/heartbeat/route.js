import { agentUrl, prepareAgentRequest } from "@/lib/varyn-agent";

export async function GET(req) {
  return proxyHeartbeat(req, "/heartbeat", { method: "GET" });
}

export async function POST(req) {
  const body = await req.json().catch(() => ({}));
  if (body.action === "dismiss" && body.noticeId) {
    return proxyHeartbeat(req, `/heartbeat/notices/${encodeURIComponent(body.noticeId)}/dismiss`, {
      method: "POST",
    }, true);
  }
  if (body.action === "run") {
    return proxyHeartbeat(req, "/heartbeat/run", { method: "POST" }, true);
  }
  return Response.json({ error: "Unknown heartbeat action." }, { status: 400 });
}

async function proxyHeartbeat(req, path, options, ownerOnly = false) {
  try {
    const access = await prepareAgentRequest(req, { ownerOnly });
    if (access.response) return access.response;
    const response = await fetch(`${agentUrl()}${path}`, {
      ...options,
      headers: access.headers(options.headers || {}),
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
