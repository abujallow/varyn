import {
  agentHeaders,
  enforceChatLimit,
  isOwnerRequest,
  ownerRequiredResponse,
  scopedSessionId,
  securityReadyForProduction,
  securityUnavailableResponse,
} from "@/lib/varyn-security";

export const DEFAULT_AGENT_URL = "http://127.0.0.1:8788";

export async function prepareAgentRequest(
  req,
  { ownerOnly = false, rateLimit = false, sessionId = "browser-session" } = {},
) {
  if (!securityReadyForProduction()) {
    return { response: securityUnavailableResponse() };
  }
  const owner = await isOwnerRequest();
  if (ownerOnly && !owner) {
    return { response: ownerRequiredResponse() };
  }
  let rateLimitHeaders = {};
  if (rateLimit) {
    const result = await enforceChatLimit(req, sessionId, owner);
    if (result.blocked) return { response: result.response };
    rateLimitHeaders = result.headers || {};
  }
  return {
    owner,
    role: owner ? "owner" : "demo",
    sessionId: scopedSessionId(req, sessionId),
    rateLimitHeaders,
    headers: (extra = {}) => agentHeaders(owner ? "owner" : "demo", extra),
  };
}

export function agentUrl() {
  return process.env.VARYN_AGENT_URL || DEFAULT_AGENT_URL;
}
