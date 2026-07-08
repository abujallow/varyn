import { agentUrl, prepareAgentRequest } from "@/lib/varyn-agent";

export async function GET(req) {
  try {
    const access = await prepareAgentRequest(req);
    if (access.response) return access.response;
    const response = await fetch(`${agentUrl()}/config/public`, {
      headers: access.headers(),
      cache: "no-store",
      signal: AbortSignal.timeout(3000),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      return Response.json({ error: data.detail || "Configuration unavailable." }, { status: response.status });
    }
    return Response.json(data, { headers: { "Cache-Control": "no-store" } });
  } catch (error) {
    console.error("Varyn configuration proxy error:", error);
    return Response.json({ error: "The local Varyn configuration service is unavailable." }, { status: 503 });
  }
}
