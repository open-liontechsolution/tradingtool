# Trading Tools Laboratory

Plataforma full-stack para **descargar datos históricos de Binance Spot**, almacenarlos en SQLite, calcular métricas técnicas y ejecutar **backtests de estrategias** con una interfaz web en React.

## Qué incluye el proyecto

- **Backend (FastAPI)** con API REST para:
  - Descarga asíncrona de velas históricas y seguimiento de progreso.
  - Consulta de velas almacenadas.
  - Cálculo y persistencia de métricas derivadas.
  - Ejecución y exportación de backtests.
- **Motor de backtesting** con registro de operaciones, curva de equity y resumen de performance.
- **Registro de estrategias** extensible (actualmente: `breakout` y `support_resistance`).
- **Frontend (React + Vite)** con dos módulos principales:
  - **Data Manager** para gestión de datos y jobs de descarga.
  - **Backtesting** para configuración, ejecución y análisis de resultados.

## Arquitectura (visión general)

- `backend/app.py`: inicializa FastAPI, CORS, rutas y (si existe) sirve `frontend/dist` como sitio estático.
- `backend/api/data_routes.py`: endpoints de datos, jobs de descarga y métricas.
- `backend/api/backtest_routes.py`: endpoints de estrategias y backtesting.
- `backend/download_engine.py`: descarga en lotes desde Binance y upsert en SQLite.
- `backend/metrics_engine.py`: cálculo de indicadores técnicos y guardado en `derived_metrics`.
- `backend/backtest_engine.py`: simulación de estrategias sobre velas históricas.
- `backend/database.py`: esquema y acceso a DB SQLite (`data/trading_tools.db` por defecto).
- `frontend/`: SPA React con Vite y proxy `/api -> http://localhost:8000` en desarrollo.

## Requisitos

- Python **3.11+** (recomendado)
- Node.js **18+** (recomendado)
- npm

## Puesta en marcha local

### 1) Backend

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

El backend queda disponible en `http://localhost:8000`.

> La base de datos se crea automáticamente al iniciar. Puedes cambiar la ruta con la variable de entorno `DB_PATH`.

### 2) Frontend (modo desarrollo)

En otra terminal:

```bash
cd frontend
npm install
npm run dev
```

La app se abre normalmente en `http://localhost:5173`.

## Build y despliegue unificado

Para servir frontend y backend desde el mismo proceso FastAPI:

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

- `GET /api/pairs` → pares disponibles.
- `POST /api/download` → inicia job de descarga.
- `GET /api/download/{job_id}` → estado/progreso/log del job.
- `GET /api/download/{job_id}/cancel` → cancela job.
- `GET /api/candles` → consulta velas almacenadas.
- `GET /api/rate-limit` → estado de peso/rate limit del cliente Binance.
- `POST /api/metrics/compute` → calcula y guarda métricas derivadas.
- `GET /api/coverage` → cobertura almacenada por símbolo/intervalo.
- `GET /api/metrics/status` → conteo de métricas guardadas.

### Backtesting

- `GET /api/strategies` → estrategias registradas y parámetros.
- `POST /api/backtest` → ejecuta backtest y devuelve `id`.
- `GET /api/backtest/{backtest_id}` → detalle completo del resultado.
- `GET /api/backtest/{backtest_id}/export?format=json|csv` → exportación de operaciones.

## Estrategias actuales

- `breakout`
  - Ruptura por cierre de máximos/mínimos previos.
  - Stop porcentual configurable.
  - Salidas por ruptura contraria.
- `support_resistance`
  - Niveles de soporte/resistencia mediante zigzag sin lookahead.
  - Entradas por quiebre y stops porcentuales.

## Métricas derivadas disponibles (motor de métricas)

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

El repositorio incluye tests unitarios para backtest, métricas, descarga y estrategias en `tests/`.

## Estructura rápida del repositorio

```text
backend/
  api/
  strategies/
  app.py
  backtest_engine.py
  download_engine.py
  metrics_engine.py
  database.py
frontend/
  src/components/
  package.json
run.py
tests/
requirements.txt
```

## Próximas mejoras sugeridas

- Añadir autenticación y control de acceso para entornos multiusuario.
- Persistir resultados de backtests en DB (actualmente se almacenan en memoria).
- Incluir Docker Compose para entorno reproducible (backend + frontend + volumen DB).
- Incorporar CI (lint + tests) y badges en este README.
