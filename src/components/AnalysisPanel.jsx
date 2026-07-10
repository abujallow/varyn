export default function AnalysisPanel({ analysis, onDismiss }) {
  const riskModules = analysis?.modules || [];

  return (
    <aside className="analysis-panel" aria-live="polite">
      <div className="analysis-header">
        <div>
          <div className="panel-label">Generated analysis</div>
          <h1>{analysis.title}</h1>
        </div>
        <button className="icon-button" onClick={onDismiss} type="button" aria-label="Dismiss analysis">
          X
        </button>
      </div>
      <p>{analysis.summary}</p>
      <div className="analysis-meta">
        {analysis.score_available === true && analysis.overall_score != null && (
          <span>Overall {analysis.overall_score}</span>
        )}
        {analysis.source && <span>{analysis.source}</span>}
        {analysis.location && <span>{analysis.location}</span>}
        {analysis.data_confidence && <span>Data confidence: {analysis.data_confidence}</span>}
      </div>
      {analysis.score_available === false && (
        <div className="score-unavailable-note">
          <strong>Insufficient data to calculate a reliable score.</strong>
          {analysis.data_gaps?.length > 0 && (
            <p>Missing: {analysis.data_gaps.join(", ")}</p>
          )}
        </div>
      )}
      {analysis.data_points?.length > 0 && (
        <div className="market-data-grid">
          {analysis.data_points.map((point) => (
            <article key={point.symbol}>
              <strong>{point.symbol}</strong>
              <span>{point.source}</span>
              <p>Price: {point.price}</p>
              <p>Move: {point.change_percent}%</p>
              <p>Beta: {point.beta}</p>
              <p>Debt/Equity: {point.debt_to_equity}</p>
              <p>Current ratio: {point.current_ratio}</p>
            </article>
          ))}
        </div>
      )}
      <div className="module-grid">
        {riskModules.map((module) => (
          <article className="risk-module" key={module.title}>
            <span>{module.score != null ? module.score : module.level || "Unrated"}</span>
            <strong>{module.title}</strong>
            <p>{module.detail}</p>
          </article>
        ))}
      </div>
      {analysis.drivers?.length > 0 && (
        <div className="driver-list">
          <strong>Key drivers</strong>
          {analysis.drivers.map((driver) => (
            <span key={driver}>{driver}</span>
          ))}
        </div>
      )}
      <div className="action-list">
        {(analysis.actions || []).map((action) => (
          <span key={action}>{action}</span>
        ))}
      </div>
      <button className="control-button clear-analysis" onClick={onDismiss} type="button">
        Clear analysis
      </button>
    </aside>
  );
}
