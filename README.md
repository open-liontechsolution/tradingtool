# Trading Tools Laboratory

Plataforma full-stack para **descargar datos históricos de Binance Spot**, almacenarlos en base de datos, calcular métricas técnicas, ejecutar **backtests** y operar un flujo de **señales + simulación/live tracking** desde una interfaz web en React, con **alertas opcionales por Telegram**.

## Qué incluye

- **Backend (FastAPI, Python 3.13)** con API REST para:
  - Descarga asíncrona de velas históricas y seguimiento de progreso.
  - Consulta de velas y cobertura almacenada.
  - Cálculo y persistencia de métricas derivadas.
  - Ejecución y exportación de backtests.
  - Gestión de configuraciones de señales, señales generadas, `sim_trades` y `real_trades`.
  - Vinculación de cuenta con Telegram y envío de alertas por bot.
- **Motores en background** al iniciar la app:
  - `run_signal_scanner()` evalúa configuraciones activas y genera señales.
  - `run_live_tracker()` actualiza estado de operaciones simuladas con datos en tiempo real y emite notificaciones de entrada, salida y stop.
- **Motor de backtesting** con curva de equity, trade log y resumen de performance.
- **Registro de estrategias extensible** (actualmente `breakout` y `support_resistance`).
- **Frontend (React 19 + Vite)** con cuatro módulos:
  - **Data Manager** (admin)
  - **Backtesting**
  - **Signals**
  - **Profile** (vinculación con Telegram)
- **Autenticación Keycloak (OIDC)** con modo mock para desarrollo local.
- **Scoping por usuario**: `signal_configs` se asocia al `user_id` del autenticado; todas las consultas y mutaciones se filtran.

## Arquitectura (visión general)

- `backend/app.py`: inicializa FastAPI, CORS, rutas y tareas de background (scanner/tracker). Sirve `frontend/dist` si existe.
- `backend/api/data_routes.py`: endpoints de datos, descarga y métricas.
- `backend/api/backtest_routes.py`: endpoints de estrategias y backtesting.
- `backend/api/signal_routes.py`: CRUD de `signal_configs`, señales y sim/real trades. Acepta `telegram_enabled` por configuración.
- `backend/api/profile_routes.py`: estado y vinculación del chat de Telegram del usuario.
- `backend/api/telegram_routes.py`: webhook público del bot de Telegram (autenticado con secret en path + header).
- `backend/download_engine.py`: descarga en lotes desde Binance y upsert en DB.
- `backend/metrics_engine.py`: cálculo de indicadores técnicos y persistencia en `derived_metrics`.
- `backend/signal_engine.py`: evaluación de estrategias para emitir señales.
- `backend/live_tracker.py`: seguimiento de señales/sim trades abiertos; dispara notificaciones al cerrar ciclo.
- `backend/notifications.py`: dispatcher único que decide destinatario, deduplica en `notification_log` y formatea mensajes.
- `backend/telegram_client.py`: cliente fino del Bot API de Telegram (no-op si `TELEGRAM_BOT_TOKEN` está vacío).
- `backend/database.py`: acceso unificado para SQLite (dev) y PostgreSQL (prod vía Alembic).
- `backend/auth.py`: auth OIDC con Keycloak + modo mock cuando `AUTH_ENABLED=false`.
- `frontend/`: SPA React con Vite y proxy `/api -> http://localhost:8000` en desarrollo.

Más detalle para contribuir está en [CLAUDE.md](./CLAUDE.md).

## Requisitos

- Python **3.13**
- Node.js **22**
- npm

## Configuración

Variables de entorno relevantes. Las sensibles (token del bot, URL del webhook, etc.) deben ir en un secreto, nunca versionadas.

### Base de datos

- `DATABASE_URL`: si empieza por `postgresql://`, se usa PostgreSQL (schema vía Alembic). Si no está definida, se usa SQLite local.
- `DB_PATH`: ruta del SQLite local (default `data/trading_tools.db`).

### Servidor

- `HOST` (default `0.0.0.0`), `PORT` (default `8000`), `LOG_LEVEL` (default `info`).
- `CORS_ORIGINS` (default `*`, lista separada por comas).
- `PUBLIC_BASE_URL`: URL pública de la app. La usa el dispatcher de notificaciones para construir el enlace "Ver trade" en los mensajes de Telegram.

### Auth backend (Keycloak)

- `AUTH_ENABLED`: `false` (default) → modo mock local; `true` → validación real de JWT.
- `KEYCLOAK_URL` (ej: `https://auth.midominio.com`)
- `KEYCLOAK_REALM` (default `tradingtool-dev`)
- `KEYCLOAK_AUDIENCE` (default `tradingtool-api`)
- `KEYCLOAK_FRONTEND_CLIENT_ID` (default `tradingtool-web`) — se expone al frontend vía `/api/auth/config`.

### Auth frontend (Vite, `frontend/.env.development.local`)

- `VITE_AUTH_ENABLED` (default `false`)
- `VITE_KEYCLOAK_URL`
- `VITE_KEYCLOAK_REALM` (default `tradingtool-dev`)
- `VITE_KEYCLOAK_CLIENT_ID` (default `tradingtool-web`)

> Para usar auth real activa backend y frontend a la vez (`AUTH_ENABLED=true` y `VITE_AUTH_ENABLED=true`).

### Notificaciones por Telegram (opcionales)

El subsistema es **totalmente inerte** si `TELEGRAM_BOT_TOKEN` está vacío: ni envía mensajes ni intenta registrar el webhook. Este es el comportamiento por defecto en dev y el que usan los tests / CI.

- `TELEGRAM_BOT_TOKEN` **(secreto)**: token del bot emitido por [@BotFather](https://t.me/BotFather).
- `TELEGRAM_BOT_USERNAME`: username del bot sin la arroba (ej. `tradingtools_dev_bot`). Se usa para construir el deep-link `https://t.me/<bot>?start=<token>`.
- `TELEGRAM_WEBHOOK_SECRET` **(secreto)**: cadena aleatoria — va en la URL del webhook y en la cabecera `X-Telegram-Bot-Api-Secret-Token` que Telegram echo-ea en cada POST.
- `TELEGRAM_WEBHOOK_URL` **(secreto, porque contiene el secret)**: URL pública HTTPS completa del webhook, con el secret en la ruta. Si se define, al arrancar la app llama a `setWebhook`.

Flujo de vinculación:

1. El usuario pulsa **Vincular Telegram** en su perfil → backend emite un token de un solo uso (TTL 15 min).
2. El usuario envía `/start <token>` al bot.
3. El webhook consume el token y asocia el `chat_id` al usuario.
4. A partir de ahí, cualquier `signal_config` con `telegram_enabled=true` envía alertas al chat en eventos de entrada, salida por estrategia o stop alcanzado.

## Puesta en marcha local

### 1) Backend

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
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

Frontend disponible en `http://localhost:5173` con proxy a `:8000`.

### 3) Migraciones (solo PostgreSQL)

En SQLite el esquema se crea inline al arrancar. En PostgreSQL se corren migraciones Alembic automáticamente en el lifespan, pero también puedes ejecutarlas a mano:

```bash
DATABASE_URL=postgresql://... alembic upgrade head
```

## Build y despliegue unificado

Para servir frontend + backend desde FastAPI:

```bash
cd frontend && npm install && npm run build && cd ..
python run.py
```

Si `frontend/dist` existe, FastAPI lo monta en `/` automáticamente.

## Endpoints principales

### Datos (admin)

> Requieren rol `app_admin`.

- `GET /api/pairs`
- `POST /api/download`
- `GET /api/download/{job_id}`
- `GET /api/download/{job_id}/cancel`
- `GET /api/candles`
- `GET /api/rate-limit`
- `POST /api/metrics/compute`
- `GET /api/coverage`
- `GET /api/metrics/status`

### Backtesting (usuario autenticado)

- `GET /api/strategies`
- `POST /api/backtest`
- `GET /api/backtest/{backtest_id}`
- `GET /api/backtest/{backtest_id}/export?format=json|csv`

### Signals / Tracking (usuario autenticado, acotado al usuario)

- `POST /api/signals/configs` — acepta `telegram_enabled`
- `GET /api/signals/configs`
- `PATCH /api/signals/configs/{config_id}` — acepta `telegram_enabled`
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

### Profile / Telegram (usuario autenticado)

- `GET /api/profile/telegram` — estado de vinculación
- `POST /api/profile/telegram/link-token` — genera deep-link con TTL 15 min
- `DELETE /api/profile/telegram` — desvincula

### Telegram webhook (público — auth vía secret)

- `POST /api/telegram/webhook/{secret}` — consumido únicamente por Telegram Bot API.

### Auth

- `GET /api/auth/config` — público; devuelve la configuración OIDC que necesita el frontend.

## Estrategias actuales

- `breakout`: ruptura por cierre de máximos/mínimos previos; stop porcentual; salida por ruptura contraria.
- `support_resistance`: niveles de soporte/resistencia mediante zigzag sin lookahead; entradas por quiebre y stops porcentuales.

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

Cubren:

- descarga de datos
- cálculo de métricas
- engine de backtest
- estrategias
- live tracker y signal engine
- cliente de Telegram, dispatcher de notificaciones y webhook

## Despliegue

- Imagen multi-stage (`Dockerfile`): Node 22 Alpine construye el frontend, Python 3.13-slim corre la app.
- CD: push a `develop` → GitHub Actions publica imagen multi-arch en GHCR, escanea con **Trivy** (HIGH/CRITICAL fixables bloquean) y solo si el scan pasa actualiza `helm/env/dev.yaml` con el tag. Argo CD aplica el cambio al cluster k3s de dev.
- Chart de Helm en `helm/`; valores sensibles viven en un `Secret` externo referenciado por `existingSecret`. El template está en `helm/env/secrets.example.yaml` — copia a `helm/env/secrets.yaml` (gitignored), rellena con `printf '%s' '<value>' | base64 -w 0`, y aplícalo manualmente con `kubectl apply -f`.
- CI adicional: **Dependabot** semanal (pip, npm, github-actions, docker) y **gitleaks** en cada PR/push como red de seguridad contra leaks accidentales. Versiones pineadas: `aquasecurity/trivy-action@v0.36.0`, `gitleaks` CLI v8.21.2; Dependabot eleva los pins cuando salgan nuevas releases.

## Estructura rápida

```text
backend/
  api/
  strategies/
  app.py
  auth.py
  backtest_engine.py
  database.py
  download_engine.py
  live_tracker.py
  metrics_engine.py
  notifications.py
  signal_engine.py
  telegram_client.py
frontend/
  src/components/
  src/auth/
helm/
  env/
  templates/
alembic/
  versions/
tests/
run.py
requirements.txt
```

## Próximas mejoras sugeridas

- Persistir resultados de backtests en DB (hoy viven en memoria por `backtest_id`).
- Implementar trailing stops (issue específica abierta; el dispatcher de notificaciones ya reserva el evento `stop_moved`).
- Observabilidad básica: métricas Prometheus + dashboard de latencias del live tracker.
