# Trading Tools Laboratory

Plataforma full-stack para **descargar datos históricos de Binance Spot**, almacenarlos en base de datos, calcular métricas técnicas, ejecutar **backtests** y operar un flujo de **señales + simulación/live tracking** desde una interfaz web en React.

## Qué incluye actualmente

- **Backend (FastAPI)** con API REST para:
  - Descarga asíncrona de velas históricas y seguimiento de progreso.
  - Consulta de velas y cobertura almacenada.
  - Cálculo/persistencia de métricas derivadas.
  - Ejecución y exportación de backtests.
  - Gestión de configuraciones de señales, señales generadas, `sim_trades` y `real_trades`.
- **Motores en background al iniciar la app**:
  - `run_signal_scanner()` para evaluar configuraciones activas y generar señales.
  - `run_live_tracker()` para actualizar estado de operaciones simuladas en tiempo real.
- **Motor de backtesting** con curva de equity, trade log y resumen de performance.
- **Registro de estrategias extensible** (actualmente: `breakout` y `support_resistance`).
- **Frontend (React + Vite)** con tres módulos:
  - **Data Manager**
  - **Backtesting**
  - **Signals**

## Arquitectura (visión general)

- `backend/app.py`: inicializa FastAPI, CORS, rutas y tareas de background (scanner/tracker). También sirve `frontend/dist` si existe.
- `backend/api/data_routes.py`: endpoints de datos, descarga y métricas.
- `backend/api/backtest_routes.py`: endpoints de estrategias y backtesting.
- `backend/api/signal_routes.py`: CRUD de signal configs, consulta de señales y manejo de sim/real trades.
- `backend/download_engine.py`: descarga en lotes desde Binance y upsert en DB.
- `backend/metrics_engine.py`: cálculo de indicadores técnicos y guardado en `derived_metrics`.
- `backend/signal_engine.py`: evaluación de estrategias para emitir señales.
- `backend/live_tracker.py`: seguimiento de señales/sim trades abiertos.
- `backend/database.py`: capa de acceso unificada para SQLite y PostgreSQL.
- `frontend/`: SPA React con Vite y proxy `/api -> http://localhost:8000` en desarrollo.

## Requisitos

- Python **3.11+** (recomendado)
- Node.js **18+** (recomendado)
- npm

## Configuración

Variables de entorno relevantes:

- `DATABASE_URL` (opcional):
  - Si **no** se define, se usa SQLite local.
  - Si empieza por `postgresql://`, se usa PostgreSQL (schema vía Alembic).
- `DB_PATH` (solo SQLite): ruta del archivo DB (default: `data/trading_tools.db`).
- `HOST` (default `0.0.0.0`), `PORT` (default `8000`), `LOG_LEVEL` (default `info`).

## Puesta en marcha local

### 1) Backend

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

Backend disponible en `http://localhost:8000`.

### 2) Frontend (modo desarrollo)

En otra terminal:

```bash
cd frontend
npm install
npm run dev
```

Frontend disponible en `http://localhost:5173`.

## Build y despliegue unificado

Para servir frontend + backend desde FastAPI:

```bash
cd frontend
npm install
npm run build
cd ..
python run.py
```

Si `frontend/dist` existe, FastAPI lo monta en `/` automáticamente.

## Endpoints principales

### Datos

- `GET /api/pairs`
- `POST /api/download`
- `GET /api/download/{job_id}`
- `GET /api/download/{job_id}/cancel`
- `GET /api/candles`
- `GET /api/rate-limit`
- `POST /api/metrics/compute`
- `GET /api/coverage`
- `GET /api/metrics/status`

### Backtesting

- `GET /api/strategies`
- `POST /api/backtest`
- `GET /api/backtest/{backtest_id}`
- `GET /api/backtest/{backtest_id}/export?format=json|csv`

### Signals / Tracking

- `POST /api/signals/configs`
- `GET /api/signals/configs`
- `PATCH /api/signals/configs/{config_id}`
- `DELETE /api/signals/configs/{config_id}`
- `GET /api/signals`
- `GET /api/signals/status`
- `GET /api/signals/{signal_id}`
- `GET /api/sim-trades`
- `GET /api/sim-trades/{trade_id}`
- `POST /api/sim-trades/{trade_id}/close`
- `POST /api/real-trades`
- `GET /api/real-trades`
- `PATCH /api/real-trades/{trade_id}`
- `DELETE /api/real-trades/{trade_id}`
- `GET /api/comparison/{sim_trade_id}`

## Estrategias actuales

- `breakout`
  - Ruptura por cierre de máximos/mínimos previos.
  - Stop porcentual configurable.
  - Salidas por ruptura contraria.
- `support_resistance`
  - Niveles de soporte/resistencia mediante zigzag sin lookahead.
  - Entradas por quiebre y stops porcentuales.

## Métricas derivadas disponibles

- Retornos: `returns_log`, `returns_simple`
- Rango: `range`, `true_range`
- Medias: `sma_20/50/200`, `ema_20/50/200`
- Volatilidad: `volatility_20/50`
- ATR: `atr_14/20`
- Extremos móviles: `rolling_max_20/50`, `rolling_min_20/50`
- Canales Donchian: `donchian_upper_20/50`, `donchian_lower_20/50`

## Tests

```bash
pytest -q
```

El repositorio incluye tests unitarios en `tests/` para:

- descarga de datos
- cálculo de métricas
- engine de backtest
- estrategias
- live tracker y signal engine

## Estructura rápida

```text
backend/
  api/
  strategies/
  app.py
  backtest_engine.py
  download_engine.py
  metrics_engine.py
  signal_engine.py
  live_tracker.py
  database.py
frontend/
  src/components/
run.py
tests/
alembic/
requirements.txt
```

## Próximas mejoras sugeridas

- Persistir resultados de backtests en DB (hoy viven en memoria por `backtest_id`).
- Añadir autenticación/autorización para entornos multiusuario.
- Incorporar CI (lint + tests + build frontend) y badges.
- Añadir Docker Compose para entorno reproducible (app + DB + frontend build).
