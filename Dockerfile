# ---------------------------------------------------------------------------
# Stage 1: Build frontend
# ---------------------------------------------------------------------------
FROM node:22-alpine AS frontend-build

WORKDIR /app/frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --production=false

COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2: Production image
# ---------------------------------------------------------------------------
FROM python:3.13-slim AS production

# Create non-root user
RUN groupadd --gid 1001 appgroup \
    && useradd --uid 1001 --gid appgroup --no-create-home appuser

WORKDIR /app

# Install Python dependencies as root before switching user
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend source
COPY backend/ ./backend/
COPY run.py ./

# Copy Alembic config (used to run migrations against PostgreSQL)
COPY alembic.ini ./
COPY alembic/ ./alembic/

# Copy frontend build artifacts
COPY --from=frontend-build /app/frontend/dist ./frontend/dist

# Create data directory for SQLite (local dev)
RUN mkdir -p data && chown -R appuser:appgroup /app

USER appuser

EXPOSE 8000

CMD ["python", "run.py"]
