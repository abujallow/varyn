import { sourceHealthTitle, sourceStatusLabel } from "../app/systemHealth";

export default function SystemPanel({ telemetry, status, sourceHealth, system }) {
  return (
    <aside className="system-panel left-panel" aria-label="System monitor">
      <div className="panel-heading">Sys Monitor</div>
      <div className="telemetry-note">
        {telemetry.source === "psutil"
          ? "Live local telemetry"
          : telemetry.source === "unavailable"
            ? "Telemetry unavailable"
            : "Telemetry connecting"}
      </div>
      <div className="status-readout" aria-live="polite">
        <span>Runtime</span>
        <strong>{status}</strong>
      </div>
      {[
        ["CPU", typeof telemetry.cpu === "number" ? `${telemetry.cpu}%` : "N/A", typeof telemetry.cpu === "number" ? telemetry.cpu : 0, "cyan"],
        ["MEM", typeof telemetry.mem === "number" ? `${telemetry.mem}%` : "N/A", typeof telemetry.mem === "number" ? telemetry.mem : 0, typeof telemetry.mem === "number" && telemetry.mem > 62 ? "amber" : "cyan"],
        ["NET", typeof telemetry.net === "number" ? `${telemetry.net}KB/s` : "N/A", telemetry.netLevel, "green"],
      ].map(([label, value, level, tone]) => (
        <div className={`metric-row metric-${tone}`} key={label}>
          <div>
            <span>{label}</span>
            <strong>{value}</strong>
          </div>
          <i style={{ "--level": `${level}%` }} />
        </div>
      ))}
      <div className="mini-readout">
        <span>UP</span>
        <strong>{telemetry.uptime}</strong>
        <span>PROC</span>
        <strong>{telemetry.proc}</strong>
        <span>OS</span>
        <strong>{telemetry.os}</strong>
      </div>
      <div className="panel-heading data-health-heading">Data Health</div>
      <div className={`source-health source-${sourceHealth.overall}`}>
        {[
          ["yfinance", sourceHealth.sources.yfinance],
          ["SEC EDGAR", sourceHealth.sources.sec_edgar],
          ["FRED", sourceHealth.sources.fred],
          ["CFPB", sourceHealth.sources.cfpb],
        ].map(([label, source]) => (
          <div
            className={`source-health-row source-status-${source?.status || "unknown"}`}
            key={label}
            title={sourceHealthTitle(label, source)}
          >
            <span>{label}</span>
            <strong>{sourceStatusLabel(source)}</strong>
            <small>{source ? `${Math.round((1 - Number(source.error_rate || 0)) * 100)}%` : "--"}</small>
          </div>
        ))}
      </div>
      <div className="panel-heading status-heading">Agent Status</div>
      {[
        ["Agent", system.agent],
        ["Provider", system.provider],
        ["Model", system.model],
        ["Backend", system.backend],
        ["Memory", system.memory],
        ["Risk", system.risk],
        ["Market", system.market],
        ["Heartbeat", system.heartbeat],
        ["Voice", system.voice],
      ].map(([label, value]) => (
        <div className="status-row" key={label}>
          <span>{label}</span>
          <strong>{value}</strong>
        </div>
      ))}
      <div className="clearance-stack">
        <span>AI Core Active</span>
        <span>Local Agent 8788</span>
        <span>Protocol Varyn</span>
      </div>
    </aside>
  );
}
