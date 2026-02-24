# Trading Tools Laboratory - Task Breakdown

## Phase 1: Project Setup & Foundation
- [ ] Initialize project structure (Vite + React frontend, Python FastAPI backend)
- [ ] Set up SQLite database with schema
- [ ] Configure project dependencies

## Phase 2: Backend - Data Layer
- [ ] Implement Binance API client with rate limiting
- [ ] Implement klines download engine with gap detection
- [ ] Implement SQLite storage layer (upsert, dedup, integrity checks)
- [ ] Implement derived metrics calculation engine
- [ ] Create REST API endpoints for data management

## Phase 3: Backend - Backtesting Engine
- [ ] Implement backtesting core engine
- [ ] Implement strategy plugin system (interface/contract)
- [ ] Implement "Breakout" initial strategy with exact rules
- [ ] Implement results metrics calculator
- [ ] Create REST API endpoints for backtesting

## Phase 4: Frontend - Dashboard
- [ ] Build Data Manager panel (pair/timeframe/range selectors, download controls)
- [ ] Build download progress & rate limit monitoring UI
- [ ] Build derived metrics panel
- [ ] Build Backtesting panel (strategy selector, dynamic inputs, results)
- [ ] Build equity/drawdown charts and trade log table
- [ ] Polish UI/UX with premium design

## Phase 5: Verification
- [ ] Test data download flow end-to-end
- [ ] Test backtesting engine with known scenarios
- [ ] Test rate limit handling
- [ ] Visual verification of dashboard
