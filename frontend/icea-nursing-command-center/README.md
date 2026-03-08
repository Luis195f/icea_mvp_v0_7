# ICEA+ Nursing Command Center (Frontend) — Next.js (App Router)

Frontend enterprise-grade (white-label) para ICEA+ v0.7.x, diseñado como **Backend-for-Frontend (BFF)** para preservar
la postura **Zero-Trust / ENS Alto**: el navegador **nunca** ve secretos HMAC.

## Requisitos
- Node.js 20+
- Backend ICEA+ (Django) ejecutándose (por defecto en `http://localhost:8000`)

## Instalación
1. Copia `.env.example` a `.env.local` y ajusta variables:
   - `ICEA_BACKEND_BASE_URL`
   - `ICEA_API_KEY` (si aplica)
   - `ICEA_AUDIT_SECRET` (si activas `ICEA_AUDIT_SIGNING_REQUIRED=true` en el backend)
2. Instala dependencias y ejecuta:
   - `npm install`
   - `npm run dev`

Abrir: `http://localhost:3000`

## Seguridad (resumen)
- Todas las llamadas del navegador van a `/api/bff/...`
- El servidor Next.js firma y aplica anti-replay (timestamp/nonce) cuando corresponde:
  - `X-ICEA-Timestamp`, `X-ICEA-Nonce`, `X-ICEA-Signature`
- Se añade `Cache-Control: no-store` a respuestas para evitar caché de PHI.

## Funcionalidad (MVP comercial)
- **Centro de mando**: KPIs y estado operacional (Donabedian: estructura/proceso/resultado)
- **Pacientes**: predicción conformal on-demand + explicación tipo SHAP (vía `/icea/compute/`)
- **Dotación**: resumen de roster + subida de CSV (si backend habilitado)
- **Causal**: descubrir DAG, ejecutar análisis causal y simular gemelo digital
- **Gobernanza**: auditoría, decisiones HITL, writebacks

## White-label
- Tokens por CSS variables en `app/globals.css`
- Tenants de ejemplo en `public/tenants/*.json`
