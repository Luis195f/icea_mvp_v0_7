# ICEA+ Nursing Command Center (Frontend) — Next.js (App Router)

Frontend demo/pilot-readiness limitada (white-label) para ICEA+ v0.7.x, diseñado como **Backend-for-Frontend (BFF)** para preservar
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
- **Pacientes**: estado y gobernanza shadow-only; sin predicciones, scores ni contribuciones individuales
- **Dotación**: resumen de roster + subida de CSV (si backend habilitado)
- **Causal**: análisis exploratorio agregado y simulación no validada clínicamente, si los flags lo habilitan
- **Gobernanza**: auditoría, decisiones HITL, writebacks

ICEA/ICEA+ is shadow-only, aggregate-only, non-individual, non-punitive, not clinically validated, not MDR production-ready, and not a clinical decision tool. No paid services are required for this demo hardening.

## White-label
- Tokens por CSS variables en `app/globals.css`
- Tenants de ejemplo en `public/tenants/*.json`
