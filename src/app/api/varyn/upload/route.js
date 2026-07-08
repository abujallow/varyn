import { agentUrl, prepareAgentRequest } from "@/lib/varyn-agent";

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
    const contentLength = Number(req.headers.get("content-length") || 0);
    const maxBytes = Number(process.env.VARYN_MAX_UPLOAD_BYTES || 10 * 1024 * 1024);
    if (contentLength > maxBytes + 1024 * 1024) {
      return Response.json({ error: "Upload exceeds the 10 MB limit." }, { status: 413 });
    }
    const incoming = await req.formData();
    const file = incoming.get("file");

    if (!file || typeof file === "string") {
      return Response.json({ error: "No file provided." }, { status: 400 });
    }

    const access = await prepareAgentRequest(req, {
      ownerOnly: true,
      sessionId: incoming.get("sessionId"),
    });
    if (access.response) return access.response;

    const formData = new FormData();
    formData.append("file", file);
    formData.append("session_id", access.sessionId);

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), AGENT_TIMEOUT_MS);
    let response;
    try {
      response = await fetch(`${agentUrl()}/upload`, {
        method: "POST",
        headers: access.headers(),
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
