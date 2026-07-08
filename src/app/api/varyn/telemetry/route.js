import { agentUrl, prepareAgentRequest } from "@/lib/varyn-agent";

export async function GET(req) {
  try {
    const access = await prepareAgentRequest(req);
    if (access.response) return access.response;
    const response = await fetch(`${agentUrl()}/telemetry`, {
      headers: access.headers(),
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
