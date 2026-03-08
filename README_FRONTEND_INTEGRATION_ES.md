# Integración del Nursing Command Center (Frontend) con ICEA+ v0.7.4

## Objetivo
Exponer un **frontend de ventas/operación clínica** (Next.js) sin romper el diseño **Zero‑Trust / ENS Alto**:
- El navegador nunca recibe secretos HMAC.
- El servidor Next (BFF) firma/anti‑replay hacia el backend.

## Ejecución rápida (desarrollo)
1. Copia `.env.example` -> `.env.dev` (backend) y ajusta secretos.
2. Ejecuta:
   - `docker compose -f docker-compose.dev.yml up --build`
3. Abrir:
   - Backend: `http://localhost:8000/api/v1/dashboard/summary/`
   - Frontend (NCC): `http://localhost:3000`
   - Dashboard técnico (Streamlit): `http://localhost:8501`

## Variables críticas del Frontend
Dentro de `docker-compose.dev.yml` el servicio `ncc` usa:
- `ICEA_BACKEND_BASE_URL=http://web:8000`
- `ICEA_API_KEY` (si el backend exige API Key)
- `ICEA_AUDIT_SECRET` (si `ICEA_AUDIT_SIGNING_REQUIRED=true`)

## Producción (orientativo)
Para ENS Alto se recomienda terminar con:
- Reverse proxy TLS (Nginx/Traefik) + mTLS interno si aplica.
- Registro de auditoría centralizado (SIEM).
- Gestión de secretos (vault/KMS).
