const DEFAULT_AGENT_URL = "http://127.0.0.1:8788";

export async function POST(req) {
  try {
    const body = await req.json().catch(() => ({}));
    const agentUrl = process.env.VARYN_AGENT_URL || DEFAULT_AGENT_URL;
    let target;
    let payload;

    if (body.action === "resolve" && body.confirmationId) {
      target = `${agentUrl}/confirmations/${encodeURIComponent(body.confirmationId)}`;
      payload = { session_id: body.sessionId || "local-preview", decision: body.decision };
    } else if (body.action === "proactive") {
      target = `${agentUrl}/safety/proactive`;
      payload = { session_id: body.sessionId || "local-preview", paused: Boolean(body.paused) };
    } else {
      return Response.json({ error: "Unknown safety action." }, { status: 400 });
    }

    const response = await fetch(target, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      cache: "no-store",
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      return Response.json(
        { error: data.detail || data.error || "Safety action failed." },
        { status: response.status },
      );
    }
    return Response.json(data, { headers: { "Cache-Control": "no-store" } });
  } catch (error) {
    console.error("Varyn safety proxy error:", error);
    return Response.json({ error: "The local Varyn safety service is unavailable." }, { status: 503 });
  }
}
