"use client";

import { useMemo, useState } from "react";

const scenarios = [
  {
    id: "market",
    label: "Market Risk",
    prompt: "Varyn, summarize market risk on the Dow Jones.",
    response:
      "Market conditions show moderate risk. Index breadth is narrowing, volatility is elevated, and rate-sensitive sectors are carrying the largest downside exposure.",
    location: "US market grid",
    modules: [
      ["Volatility", "68", "Implied volatility remains above the 30-day baseline."],
      ["Sector Stress", "61", "Industrials and financials show mild correlation pressure."],
      ["VaR Shift", "+3.8%", "One-day portfolio value at risk increased from the prior session."],
    ],
    actions: ["Monitor rate-sensitive exposure", "Run downside stress test", "Prepare morning risk brief"],
  },
  {
    id: "credit",
    label: "Merger Credit",
    prompt: "Varyn, analyze credit risk in a hypothetical SpaceX-Tesla merger.",
    response:
      "The transaction introduces elevated credit and funding risk. Integration complexity, capital requirements, and concentration exposure are the primary drivers.",
    location: "Corporate event layer",
    modules: [
      ["Funding Risk", "74", "Projected financing needs increase refinancing sensitivity."],
      ["Concentration", "81", "Founder, sector, and balance-sheet exposure would become more correlated."],
      ["Liquidity", "66", "Cash flexibility remains usable, but downside buffers tighten."],
    ],
    actions: ["Build credit memo", "Stress liquidity runway", "Model debt-service sensitivity"],
  },
  {
    id: "location",
    label: "Location Scan",
    prompt: "Varyn, scan regional operating risk in Buffalo.",
    response:
      "Regional operating risk is stable with watch items around labor availability, public infrastructure, weather disruption, and localized economic exposure.",
    location: "Buffalo / Great Lakes corridor",
    modules: [
      ["Operational", "57", "Weather and infrastructure remain key continuity variables."],
      ["Economic", "49", "Local demand indicators are stable but uneven by sector."],
      ["Resilience", "72", "Regional institutions provide moderate support capacity."],
    ],
    actions: ["Map operational dependencies", "Review continuity plans", "Track local macro indicators"],
  },
  {
    id: "portfolio",
    label: "Portfolio Risk",
    prompt: "Varyn, build a low-risk dividend portfolio briefing.",
    response:
      "A conservative dividend screen should prioritize durable cash flow, low leverage, rate resilience, and sector diversification over headline yield.",
    location: "Portfolio construction mode",
    modules: [
      ["Income Quality", "78", "Preference for stable payout ratios and recurring cash flow."],
      ["Credit Quality", "70", "Low leverage and investment-grade balance sheets reduce downside risk."],
      ["Diversification", "64", "Avoid excessive concentration in utilities and financials."],
    ],
    actions: ["Screen dividend stability", "Compare leverage ratios", "Generate investment briefing"],
  },
];

const idleMessages = [
  "Standing by for risk command.",
  "Live intelligence layer online.",
  "Awaiting market, credit, liquidity, or location query.",
];

export default function Home() {
  const [activeScenario, setActiveScenario] = useState(null);
  const [prompt, setPrompt] = useState("");
  const [isProcessing, setIsProcessing] = useState(false);

  const scenario = activeScenario !== null ? scenarios[activeScenario] : null;

  const statusMessage = useMemo(() => {
    if (isProcessing) return "Processing request...";
    if (scenario) return `Response generated / ${scenario.label}`;
    return idleMessages[0];
  }, [isProcessing, scenario]);

  const runScenario = (index) => {
    setPrompt(scenarios[index].prompt);
    setIsProcessing(true);
    window.setTimeout(() => {
      setActiveScenario(index);
      setIsProcessing(false);
    }, 520);
  };

  const submitPrompt = (event) => {
    event.preventDefault();
    const text = prompt.toLowerCase();
    const nextIndex = text.includes("merger") || text.includes("credit")
      ? 1
      : text.includes("buffalo") || text.includes("city") || text.includes("location")
        ? 2
        : text.includes("portfolio") || text.includes("dividend")
          ? 3
          : 0;

    runScenario(nextIndex);
  };

  return (
    <main className={`varyn-shell ${scenario || isProcessing ? "is-awake" : ""}`}>
      <header className="command-header">
        <div className="brand-lockup">
          <div className="brand-ring" aria-hidden="true" />
          <div>
            <strong>VARYN</strong>
            <span>AI Risk Intelligence OS</span>
          </div>
        </div>
        <div className="system-state">
          <span>{isProcessing ? "Responding" : "Online"}</span>
          <span>Voice layer concept</span>
        </div>
      </header>

      <section className="console-frame" aria-label="Varyn live command interface">
        <div className="map-layer" aria-hidden="true">
          <div className="map-node node-a" />
          <div className="map-node node-b" />
          <div className="map-node node-c" />
          <div className="map-node node-d" />
          <div className="route route-a" />
          <div className="route route-b" />
          <div className="route route-c" />
        </div>

        <div className="center-interface">
          <div className="status-text">{statusMessage}</div>
          <div className="core-orbit">
            <div className="core-ring ring-one" />
            <div className="core-ring ring-two" />
            <button
              className="varyn-core"
              onClick={() => runScenario(0)}
              type="button"
              aria-label="Wake Varyn market risk scenario"
            >
              <span>VARYN</span>
              <small>{isProcessing ? "Processing" : scenario ? "Ready" : "Listening"}</small>
            </button>
          </div>
          <p className="voice-line">
            {scenario
              ? `"${scenario.response}"`
              : '"Good morning, Abu. I am standing by for your next risk command."'}
          </p>
        </div>

        {scenario && (
          <aside className="response-panel" aria-live="polite">
            <div className="panel-kicker">Generated analysis</div>
            <h1>{scenario.label}</h1>
            <p>{scenario.response}</p>
            <div className="location-strip">{scenario.location}</div>
            <div className="module-grid">
              {scenario.modules.map(([title, score, detail]) => (
                <article className="risk-module" key={title}>
                  <span>{score}</span>
                  <strong>{title}</strong>
                  <p>{detail}</p>
                </article>
              ))}
            </div>
            <div className="action-list">
              {scenario.actions.map((action) => (
                <span key={action}>{action}</span>
              ))}
            </div>
          </aside>
        )}

        <form className="prompt-dock" onSubmit={submitPrompt}>
          <label htmlFor="varyn-prompt">Command Varyn</label>
          <div className="prompt-row">
            <input
              id="varyn-prompt"
              onChange={(event) => setPrompt(event.target.value)}
              placeholder="Ask about market risk, credit risk, a city, or a portfolio..."
              value={prompt}
            />
            <button type="submit">{isProcessing ? "Analyzing" : "Send"}</button>
          </div>
          <div className="suggestions">
            {scenarios.map((item, index) => (
              <button key={item.id} onClick={() => runScenario(index)} type="button">
                {item.prompt}
              </button>
            ))}
          </div>
        </form>
      </section>

      <section className="vision-strip" aria-label="Varyn product vision">
        <article>
          <span>Mission</span>
          <p>
            Make institutional-grade risk intelligence accessible through conversational AI and
            executive-ready reporting.
          </p>
        </article>
        <article>
          <span>Future voice layer</span>
          <p>
            Designed for a calm, professional assistant tone that can brief, explain, and escalate
            risk in real time.
          </p>
        </article>
        <article>
          <span>Operating model</span>
          <p>Voice interface, AI agent layer, risk engine, data aggregation, and reports.</p>
        </article>
      </section>
    </main>
  );
}
