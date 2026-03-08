# ICEA+ Super Árbol (Plataforma + Frontend)

Este repositorio empaqueta la plataforma ICEA+ v0.7.4 (backend + scheduler + dashboard técnico) y el Frontend comercial (Nursing Command Center) en un único artefacto.

## Árbol de directorios (alto nivel)

```
icea_mvp_v0_7/
├── backend/                      # Django API (ICEA+)
├── dashboard/                    # Streamlit (panel técnico/ops)
├── scheduler/                    # Entrenamiento programado
├── frontend/
│   └── icea-nursing-command-center/  # Next.js (BFF + UI comercial ES)
├── docker-compose.yml            # Base (seguro) + perfil ui
├── docker-compose.dev.yml        # Desarrollo (incluye ui)
├── docker-compose.prod.yml       # Producción/ENS (incluye ui opcional)
└── SUPER_TREE.md                 # Este documento
```

## Puertos por defecto
- Backend: 8000
- Dashboard técnico (Streamlit): 8501
- Nursing Command Center (Next.js): 3000
