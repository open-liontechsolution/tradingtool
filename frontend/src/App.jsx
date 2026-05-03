import { useState, useMemo } from 'react'
import { useAuth } from './auth/useAuth'
import DataManager from './components/DataManager'
import BacktestPanel from './components/BacktestPanel'
import SignalsPanel from './components/SignalsPanel'
import ProfilePanel from './components/ProfilePanel'
import RecommendedInvestmentPanel from './components/RecommendedInvestmentPanel'

function App() {
  const { isAuthenticated, isLoading, login, logout, user, isAdmin } = useAuth()

  const tabs = useMemo(() => {
    const t = []
    if (isAdmin) t.push({ id: 'data', label: 'Data Manager' })
    t.push({ id: 'backtest', label: 'Backtesting' })
    t.push({ id: 'signals', label: 'Signals' })
    t.push({ id: 'recommendations', label: 'Inversión recomendada' })
    t.push({ id: 'profile', label: 'Profile' })
    return t
  }, [isAdmin])

  const [activeTab, setActiveTab] = useState(isAdmin ? 'data' : 'signals')
  const [navOpen, setNavOpen] = useState(false)

  const selectTab = (id) => {
    setActiveTab(id)
    setNavOpen(false)
  }

  // Loading state
  if (isLoading) {
    return (
      <div className="app-layout" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: '100vh' }}>
        <p style={{ color: 'var(--text-secondary)', fontSize: '1rem' }} role="status" aria-live="polite">Loading...</p>
      </div>
    )
  }

  // Not authenticated — login screen
  if (!isAuthenticated) {
    return (
      <div className="app-layout" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: '100vh' }}>
        <div style={{ textAlign: 'center', maxWidth: 420, padding: '0 var(--space-4)' }}>
          <div className="topbar-logo" style={{ margin: '0 auto var(--space-4)', width: 48, height: 48, fontSize: '1.5rem' }} aria-hidden="true">
            <span>T</span>
          </div>
          <h1 style={{ color: 'var(--text-primary)', fontSize: '1.5rem', marginBottom: 'var(--space-2)' }}>Trading Tools Laboratory</h1>
          <p style={{ color: 'var(--text-secondary)', marginBottom: 'var(--space-5)', lineHeight: 1.5 }}>
            Plataforma para descargar datos históricos de Binance, ejecutar backtests y monitorizar señales y trades simulados en vivo.
          </p>
          <button className="btn btn-primary" onClick={login} style={{ padding: '0.6rem 2rem', fontSize: '1rem' }}>
            Sign in
          </button>
        </div>
      </div>
    )
  }

  const username = user?.profile?.preferred_username || user?.profile?.email || 'User'

  return (
    <div className="app-layout">
      <header className="topbar">
        <div className="topbar-brand">
          <div className="topbar-logo">
            <span>T</span>
          </div>
          <div>
            <div className="topbar-title">Trading Tools</div>
            <div className="topbar-subtitle">Laboratory</div>
          </div>
        </div>

        <button
          type="button"
          className="topbar-burger"
          aria-label={navOpen ? 'Close navigation' : 'Open navigation'}
          aria-expanded={navOpen}
          aria-controls="primary-nav"
          onClick={() => setNavOpen(o => !o)}
        >
          <span aria-hidden="true">{navOpen ? '✕' : '☰'}</span>
        </button>

        <nav id="primary-nav" className={`tab-nav${navOpen ? ' tab-nav--open' : ''}`}>
          {tabs.map(tab => (
            <button
              key={tab.id}
              className={`tab-btn${activeTab === tab.id ? ' active' : ''}`}
              onClick={() => selectTab(tab.id)}
            >
              {tab.label}
            </button>
          ))}
        </nav>

        <div className="topbar-user">
          <span className="topbar-username">{username}</span>
          <button
            onClick={logout}
            style={{
              background: 'transparent',
              border: '1px solid var(--text-muted)',
              color: 'var(--text-secondary)',
              padding: '0.3rem 0.75rem',
              borderRadius: 'var(--radius-sm)',
              cursor: 'pointer',
              fontSize: '0.8rem',
            }}
          >
            Logout
          </button>
        </div>
      </header>

      <main className="main-content">
        {activeTab === 'data'     && isAdmin && <DataManager />}
        {activeTab === 'backtest' && <BacktestPanel />}
        {activeTab === 'signals'  && <SignalsPanel />}
        {activeTab === 'recommendations' && <RecommendedInvestmentPanel selectTab={selectTab} />}
        {activeTab === 'profile'  && <ProfilePanel />}
      </main>
    </div>
  )
}

export default App
