import { agentUrl, prepareAgentRequest } from "@/lib/varyn-agent";

export async function POST(req) {
  try {
    const body = await req.json().catch(() => ({}));
    const sessionId = body.sessionId || "local-preview";
    const action = body.action || "reset";
    const access = await prepareAgentRequest(req, { ownerOnly: true, sessionId });
    if (access.response) return access.response;

    const target =
      action === "clear-file"
        ? `${agentUrl()}/files/${encodeURIComponent(access.sessionId)}`
        : `${agentUrl()}/session/reset`;

    const response = await fetch(target, {
      method: action === "clear-file" ? "DELETE" : "POST",
      headers: access.headers(action === "clear-file" ? {} : { "Content-Type": "application/json" }),
      body: action === "clear-file" ? undefined : JSON.stringify({ session_id: access.sessionId }),
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
