import { useState } from 'react'
import DataManager from './components/DataManager'
import BacktestPanel from './components/BacktestPanel'
import SignalsPanel from './components/SignalsPanel'

const TABS = [
  { id: 'data', label: 'Data Manager' },
  { id: 'backtest', label: 'Backtesting' },
  { id: 'signals', label: 'Signals' },
]

function App() {
  const [activeTab, setActiveTab] = useState('data')

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
          {TABS.map(tab => (
            <button
              key={tab.id}
              className={`tab-btn${activeTab === tab.id ? ' active' : ''}`}
              onClick={() => setActiveTab(tab.id)}
            >
              {tab.label}
            </button>
          ))}
        </nav>
      </header>

      <main className="main-content">
        {activeTab === 'data'     && <DataManager />}
        {activeTab === 'backtest' && <BacktestPanel />}
        {activeTab === 'signals'  && <SignalsPanel />}
      </main>
    </div>
  )
}

export default App
