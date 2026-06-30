const DEFAULT_AGENT_URL = "http://127.0.0.1:8788";

export async function GET() {
  try {
    const agentUrl = process.env.VARYN_AGENT_URL || DEFAULT_AGENT_URL;
    const response = await fetch(`${agentUrl}/telemetry`, {
      cache: "no-store",
      signal: AbortSignal.timeout(3000),
    });
    const data = await response.json().catch(() => ({}));

    if (!response.ok) {
      return Response.json(
        { error: data.error || "Local telemetry is unavailable." },
        { status: response.status },
      );
    }

    return Response.json(data, {
      headers: { "Cache-Control": "no-store" },
    });
  } catch (error) {
    console.error("Varyn telemetry proxy error:", error);
    return Response.json(
      {
        error: "The local Varyn telemetry agent is not running.",
        source: "unavailable",
      },
      { status: 503 },
    );
  }
}
