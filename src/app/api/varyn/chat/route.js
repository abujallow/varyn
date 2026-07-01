// TODO: When the Python backend is hosted remotely (e.g. Railway, Render, or a VPS),
// update the VARYN_AGENT_URL environment variable in Vercel project settings to point
// to the hosted agent URL. The HUD will then be fully live without requiring a local backend.

const DEFAULT_AGENT_URL = "http://127.0.0.1:8788";
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

    const agentUrl = process.env.VARYN_AGENT_URL || DEFAULT_AGENT_URL;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), AGENT_TIMEOUT_MS);
    let response;
    try {
      response = await fetch(`${agentUrl}/chat`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          message,
          session_id: body.sessionId || "local-preview",
          source: body.source || "typed",
        }),
        cache: "no-store",
        signal: controller.signal,
      });
    } finally {
      clearTimeout(timeout);
    }

    const data = await response.json().catch(() => ({}));

    if (!response.ok) {
      return offlineResponse();
    }

    return Response.json(data);
  } catch {
    return offlineResponse();
  }
}
