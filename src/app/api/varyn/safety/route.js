import { agentUrl, prepareAgentRequest } from "@/lib/varyn-agent";

export async function POST(req) {
  try {
    const body = await req.json().catch(() => ({}));
    const isResolve = body.action === "resolve" && body.confirmationId;
    // "resolve" is intentionally not owner-only here: the backend's
    // /confirmations/{id} route makes its own per-confirmation, action-aware
    // owner check (confirmation_requires_owner() in main.py) -- some
    // confirmation-gated actions (export_risk_memo) are resolvable by any
    // authenticated demo/public session, others are not. "proactive" (the
    // kill switch) stays strictly owner-only.
    const access = await prepareAgentRequest(req, {
      ownerOnly: !isResolve,
      sessionId: body.sessionId,
    });
    if (access.response) return access.response;
    let target;
    let payload;

    if (isResolve) {
      target = `${agentUrl()}/confirmations/${encodeURIComponent(body.confirmationId)}`;
      payload = { session_id: access.sessionId, decision: body.decision };
    } else if (body.action === "proactive") {
      target = `${agentUrl()}/safety/proactive`;
      payload = { session_id: access.sessionId, paused: Boolean(body.paused) };
    } else {
      return Response.json({ error: "Unknown safety action." }, { status: 400 });
    }

    const response = await fetch(target, {
      method: "POST",
      headers: access.headers({ "Content-Type": "application/json" }),
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
