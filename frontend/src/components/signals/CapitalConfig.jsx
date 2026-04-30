import FieldLabel from '../FieldLabel'

const TIPS = {
  portfolio: 'Capital inicial de la cuenta (USD). Inmutable salvo edición manual; arranca = current_portfolio y evoluciona con el PnL neto de cada sim-trade cerrado.',
  leverage: 'Apalancamiento isolated. 1 = sin leverage. >1 activa el cálculo de precio de liquidación (long: entry × (1 − 1/lev + mm); short: entry × (1 + 1/lev − mm)).',
  invested: 'Capital efectivo a desplegar por trade en USD. Se traduce internamente a leverage = invested/portfolio.',
}

export function CapitalConfig({ portfolio, setPortfolio, leverage, setLeverage, investedAmount, setInvestedAmount, mode, setMode, disabled, lockMode = null }) {
  // ``lockMode`` (#149): when set, the toggle whose value differs from
  // ``lockMode`` is disabled. Caller is responsible for forcing ``mode`` to
  // ``lockMode`` when entering the locked state — the component does not
  // self-correct to keep the side-effect surface minimal.
  const leverageLocked = lockMode !== null && lockMode !== 'leverage'
  const investedLocked = lockMode !== null && lockMode !== 'invested'
  const lockTooltip = 'En risk-based el invested_amount se ignora — sizing derivado del stop distance.'
  return (
    <div>
      <div style={{ display: 'flex', gap: 'var(--space-2)', marginBottom: 'var(--space-3)' }}>
        <button
          type="button"
          className={`btn btn-sm ${mode === 'leverage' ? 'btn-primary' : 'btn-secondary'}`}
          onClick={() => setMode('leverage')}
          disabled={disabled || leverageLocked}
          title={leverageLocked ? lockTooltip : undefined}
        >Portfolio + Leverage</button>
        <button
          type="button"
          className={`btn btn-sm ${mode === 'invested' ? 'btn-primary' : 'btn-secondary'}`}
          onClick={() => setMode('invested')}
          disabled={disabled || investedLocked}
          title={investedLocked ? lockTooltip : undefined}
        >Portfolio + Invested</button>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--space-3)' }}>
        <div className="form-group">
          <FieldLabel tooltip={TIPS.portfolio}>Portfolio (USD)</FieldLabel>
          <input type="number" className="form-control" value={portfolio} min={1} step={100}
            onChange={e => setPortfolio(parseFloat(e.target.value) || 0)} disabled={disabled} />
        </div>
        {mode === 'leverage' ? (
          <div className="form-group">
            <FieldLabel tooltip={TIPS.leverage}>Leverage</FieldLabel>
            <input type="number" className="form-control" value={leverage} min={0.1} step={0.1}
              onChange={e => setLeverage(parseFloat(e.target.value) || 1)} disabled={disabled} />
          </div>
        ) : (
          <div className="form-group">
            <FieldLabel tooltip={TIPS.invested}>Invested Amount (USD)</FieldLabel>
            <input type="number" className="form-control" value={investedAmount} min={1} step={100}
              onChange={e => setInvestedAmount(parseFloat(e.target.value) || 0)} disabled={disabled} />
          </div>
        )}
      </div>
    </div>
  )
}
