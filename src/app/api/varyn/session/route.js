const DEFAULT_AGENT_URL = "http://127.0.0.1:8788";

export async function POST(req) {
  try {
    const body = await req.json().catch(() => ({}));
    const sessionId = body.sessionId || "local-preview";
    const action = body.action || "reset";
    const agentUrl = process.env.VARYN_AGENT_URL || DEFAULT_AGENT_URL;

    const target =
      action === "clear-file"
        ? `${agentUrl}/files/${encodeURIComponent(sessionId)}`
        : `${agentUrl}/session/reset`;

    const response = await fetch(target, {
      method: action === "clear-file" ? "DELETE" : "POST",
      headers: action === "clear-file" ? undefined : { "Content-Type": "application/json" },
      body: action === "clear-file" ? undefined : JSON.stringify({ session_id: sessionId }),
    });

    const data = await response.json().catch(() => ({}));

    if (!response.ok) {
      return Response.json({ error: data.error || "Session command failed." }, { status: response.status });
    }

    return Response.json(data);
  } catch (error) {
    console.error("Varyn session proxy error:", error);
    return Response.json({ error: "The local Varyn session agent is not running." }, { status: 503 });
  }
}
