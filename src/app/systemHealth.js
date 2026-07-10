export function sourceStatusLabel(source) {
  if (!source) return "Awaiting";
  if (source.enabled === false) return "Disabled";
  const status = String(source.status || "unknown").toLowerCase();
  if (status === "active") return "Active";
  if (status === "degraded") return "Degraded";
  if (status === "unavailable") return "Unavailable";
  return "Awaiting";
}

export function sourceHealthTitle(label, source) {
  if (!source) return `${label}: awaiting first source check`;
  const lastCheck = source.last_failed_pull || source.last_successful_pull;
  const details = [
    `${label}: ${sourceStatusLabel(source)}`,
    lastCheck ? `last checked ${new Date(lastCheck).toLocaleString()}` : null,
    source.last_error || null,
  ].filter(Boolean);
  return details.join(". ");
}

export function backendLabel({ hosted, backendPort } = {}) {
  if (hosted) return "Hosted Agent";
  if (backendPort) return `Local Agent ${backendPort}`;
  return null;
}
