import {
  authenticateOwner,
  clearOwnerSession,
  enforceOwnerLoginLimit,
  isOwnerRequest,
  ownerAuthConfigured,
} from "@/lib/varyn-security";

export async function GET() {
  return Response.json(
    { owner: await isOwnerRequest(), configured: ownerAuthConfigured() },
    { headers: { "Cache-Control": "no-store" } },
  );
}

export async function POST(req) {
  const body = await req.json().catch(() => ({}));
  if (body.action === "logout") {
    await clearOwnerSession();
    return Response.json({ owner: false, configured: ownerAuthConfigured() });
  }
  const limited = await enforceOwnerLoginLimit(req);
  if (limited) return limited;
  const authenticated = await authenticateOwner(body.accessKey);
  if (!authenticated) {
    return Response.json({ error: "Owner access was not accepted." }, { status: 401 });
  }
  return Response.json({ owner: true, configured: true });
}
