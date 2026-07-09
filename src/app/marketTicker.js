/**
 * True only for a real, finite number -- deliberately not a bare Number(value)
 * check, since Number(null) and Number("") both coerce to 0 in JS and would
 * otherwise make a genuinely missing field indistinguishable from a real zero.
 */
function isRealNumber(value) {
  if (value === null || value === undefined || value === "") return false;
  return Number.isFinite(Number(value));
}

export function formatMarketPrice(value) {
  if (!isRealNumber(value)) return "Unavailable";
  const price = Number(value);
  return `$${price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function formatMarketChange(value) {
  if (!isRealNumber(value)) return "--";
  const change = Number(value);
  return `${change >= 0 ? "+" : ""}${change.toFixed(2)}%`;
}

export function formatMarketTimestamp(value) {
  if (!value) return null;
  const sampledAt = new Date(value);
  if (Number.isNaN(sampledAt.getTime())) return null;
  return sampledAt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/**
 * A ticker is displayable only when the backend explicitly marked it available --
 * never inferred from a truthiness check on price/change_percent, since 0 is a
 * legitimate value (flat price move, zero-decimal price) that must still render.
 */
export function isTickerAvailable(item) {
  return Boolean(item?.available);
}

/**
 * Dedupe a list of ticker snapshot items by symbol (first occurrence wins), or fall
 * back to a placeholder row per watchlist symbol when no snapshot data exists yet.
 */
export function buildMarketTickerItems(snapshotSymbols, watchlist) {
  const latestBySymbol = new Map();
  const sourceItems = snapshotSymbols?.length
    ? snapshotSymbols
    : (watchlist || []).map((symbol) => ({ symbol, available: false, stale: true, pinned: true }));
  sourceItems.forEach((item) => {
    const symbol = String(item?.symbol || "").toUpperCase();
    if (symbol && !latestBySymbol.has(symbol)) latestBySymbol.set(symbol, item);
  });
  return [...latestBySymbol.values()];
}
