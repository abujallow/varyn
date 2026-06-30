const DEFAULT_AGENT_URL = "http://127.0.0.1:8788";

export async function GET() {
  try {
    const agentUrl = process.env.VARYN_AGENT_URL || DEFAULT_AGENT_URL;
    const response = await fetch(`${agentUrl}/config/public`, {
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
