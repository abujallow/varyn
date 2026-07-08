import { agentUrl, prepareAgentRequest } from "@/lib/varyn-agent";

const AGENT_TIMEOUT_MS = 25000;
const OFFLINE_RESPONSE = {
  reply: "Varyn is warming up — the intelligence layer may be starting from a cold state. Please try your command again in 15 seconds.",
  mode: "offline",
  provider: "none",
};

function offlineResponse() {
  return Response.json(OFFLINE_RESPONSE, { status: 200 });
}

export async function POST(req) {
  try {
    const body = await req.json();
    const message = body?.message?.trim();
    if (!message) {
      return Response.json({ error: "No message provided." }, { status: 400 });
    }

    const access = await prepareAgentRequest(req, {
      rateLimit: true,
      sessionId: body.sessionId,
    });
    if (access.response) return access.response;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), AGENT_TIMEOUT_MS);
    let response;
    try {
      response = await fetch(`${agentUrl()}/chat/stream`, {
        method: "POST",
        headers: access.headers({ "Content-Type": "application/json" }),
        body: JSON.stringify({
          message,
          session_id: access.sessionId,
          source: body.source || "typed",
        }),
        cache: "no-store",
        signal: controller.signal,
      });
    } finally {
      clearTimeout(timeout);
    }

    if (!response.ok || !response.body) {
      return offlineResponse();
    }

    return new Response(response.body, {
      status: response.status,
      headers: {
        ...access.rateLimitHeaders,
        "Content-Type": "text/event-stream; charset=utf-8",
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
      },
    });
  } catch {
    return offlineResponse();
  }
}
