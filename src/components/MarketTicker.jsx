import {
  formatMarketChange,
  formatMarketPrice,
  formatMarketTimestamp,
  isTickerAvailable,
} from "../app/marketTicker";

export default function MarketTicker({ proactivePaused, running, items, timestamp, sampledAt }) {
  return (
    <section className="market-ticker" aria-label="Live heartbeat market watch">
      <div className="market-ticker-status">
        <span>Online</span>
        <small>{proactivePaused ? "Proactive paused" : running ? "Scanning" : "Market watch"}</small>
      </div>
      <div className="market-ticker-window">
        <div className="market-ticker-track">
          {[0, 1].map((cycleIndex) => (
            <div className="market-ticker-cycle" aria-hidden={cycleIndex === 1} key={cycleIndex}>
              {items.map((item) => {
                const change = Number(item.change_percent);
                const changeTone = Number.isFinite(change) ? (change > 0 ? "up" : change < 0 ? "down" : "flat") : "missing";
                const quoteTime = formatMarketTimestamp(item.sampled_at);
                return (
                  <article
                    className={`market-ticker-item ${item.stale ? "is-stale" : ""}`}
                    key={`${cycleIndex}-${item.symbol}`}
                    title={item.stale
                      ? `${item.symbol} last-known value${quoteTime ? ` as of ${quoteTime}` : ""}; latest refresh unavailable`
                      : `${item.symbol} latest cached heartbeat value`}
                  >
                    <strong>{item.symbol}</strong>
                    <span className="ticker-price">{isTickerAvailable(item) ? formatMarketPrice(item.price) : "Unavailable"}</span>
                    <span className={`ticker-change is-${changeTone}`}>
                      {isTickerAvailable(item) ? formatMarketChange(item.change_percent) : "--"}
                    </span>
                  </article>
                );
              })}
            </div>
          ))}
        </div>
      </div>
      <time className="market-ticker-time" dateTime={sampledAt || undefined}>
        {timestamp ? `As of ${timestamp}` : "Awaiting market scan..."}
      </time>
    </section>
  );
}
