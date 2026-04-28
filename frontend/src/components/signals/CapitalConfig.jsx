export function CapitalConfig({ portfolio, setPortfolio, leverage, setLeverage, investedAmount, setInvestedAmount, mode, setMode, disabled }) {
  return (
    <div>
      <div style={{ display: 'flex', gap: 'var(--space-2)', marginBottom: 'var(--space-3)' }}>
        <button
          type="button"
          className={`btn btn-sm ${mode === 'leverage' ? 'btn-primary' : 'btn-secondary'}`}
          onClick={() => setMode('leverage')}
          disabled={disabled}
        >Portfolio + Leverage</button>
        <button
          type="button"
          className={`btn btn-sm ${mode === 'invested' ? 'btn-primary' : 'btn-secondary'}`}
          onClick={() => setMode('invested')}
          disabled={disabled}
        >Portfolio + Invested</button>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--space-3)' }}>
        <div className="form-group">
          <label className="form-label">Portfolio (USD)</label>
          <input type="number" className="form-control" value={portfolio} min={1} step={100}
            onChange={e => setPortfolio(parseFloat(e.target.value) || 0)} disabled={disabled} />
        </div>
        {mode === 'leverage' ? (
          <div className="form-group">
            <label className="form-label">Leverage</label>
            <input type="number" className="form-control" value={leverage} min={0.1} step={0.1}
              onChange={e => setLeverage(parseFloat(e.target.value) || 1)} disabled={disabled} />
          </div>
        ) : (
          <div className="form-group">
            <label className="form-label">Invested Amount (USD)</label>
            <input type="number" className="form-control" value={investedAmount} min={1} step={100}
              onChange={e => setInvestedAmount(parseFloat(e.target.value) || 0)} disabled={disabled} />
          </div>
        )}
      </div>
    </div>
  )
}
