import { useState, useMemo } from 'react'
import { useAuth } from './auth/useAuth'
import DataManager from './components/DataManager'
import BacktestPanel from './components/BacktestPanel'
import SignalsPanel from './components/SignalsPanel'
import ProfilePanel from './components/ProfilePanel'

function App() {
  const { isAuthenticated, isLoading, login, logout, user, isAdmin } = useAuth()

  const tabs = useMemo(() => {
    const t = []
    if (isAdmin) t.push({ id: 'data', label: 'Data Manager' })
    t.push({ id: 'backtest', label: 'Backtesting' })
    t.push({ id: 'signals', label: 'Signals' })
    t.push({ id: 'profile', label: 'Profile' })
    return t
  }, [isAdmin])

  const [activeTab, setActiveTab] = useState(isAdmin ? 'data' : 'signals')

  // Loading state
  if (isLoading) {
    return (
      <div className="app-layout" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: '100vh' }}>
        <p style={{ color: '#94a3b8', fontSize: '1rem' }}>Loading...</p>
      </div>
    )
  }

  // Not authenticated — login screen
  if (!isAuthenticated) {
    return (
      <div className="app-layout" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: '100vh' }}>
        <div style={{ textAlign: 'center' }}>
          <div className="topbar-logo" style={{ margin: '0 auto 1rem', width: 48, height: 48, fontSize: '1.5rem' }}>
            <span>T</span>
          </div>
          <h1 style={{ color: '#e2e8f0', fontSize: '1.5rem', marginBottom: '0.5rem' }}>Trading Tools Laboratory</h1>
          <p style={{ color: '#94a3b8', marginBottom: '1.5rem' }}>Sign in to continue</p>
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

        <nav className="tab-nav">
          {tabs.map(tab => (
            <button
              key={tab.id}
              className={`tab-btn${activeTab === tab.id ? ' active' : ''}`}
              onClick={() => setActiveTab(tab.id)}
            >
              {tab.label}
            </button>
          ))}
        </nav>

        <div className="topbar-user">
          <span style={{ color: '#94a3b8', fontSize: '0.85rem', marginRight: '0.75rem' }}>{username}</span>
          <button
            onClick={logout}
            style={{
              background: 'transparent',
              border: '1px solid #475569',
              color: '#94a3b8',
              padding: '0.3rem 0.75rem',
              borderRadius: '4px',
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
        {activeTab === 'profile'  && <ProfilePanel />}
      </main>
    </div>
  )
}

export default App
