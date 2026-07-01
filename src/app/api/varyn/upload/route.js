const DEFAULT_AGENT_URL = "http://127.0.0.1:8788";
const AGENT_TIMEOUT_MS = 25000;
const OFFLINE_MESSAGE = "Varyn is warming up — the intelligence layer may be starting from a cold state. Please try the upload again in 15 seconds.";

function offlineResponse() {
  return Response.json(
    {
      error: OFFLINE_MESSAGE,
      reply: OFFLINE_MESSAGE,
      mode: "offline",
      provider: "none",
    },
    { status: 200 },
  );
}

export async function POST(req) {
  try {
    const incoming = await req.formData();
    const file = incoming.get("file");

    if (!file || typeof file === "string") {
      return Response.json({ error: "No file provided." }, { status: 400 });
    }

    const formData = new FormData();
    formData.append("file", file);
    formData.append("session_id", incoming.get("sessionId") || "local-preview");

    const agentUrl = process.env.VARYN_AGENT_URL || DEFAULT_AGENT_URL;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), AGENT_TIMEOUT_MS);
    let response;
    try {
      response = await fetch(`${agentUrl}/upload`, {
        method: "POST",
        body: formData,
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
