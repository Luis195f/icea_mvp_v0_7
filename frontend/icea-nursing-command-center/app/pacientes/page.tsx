"use client";

import React from "react";
import { InlineNotification, Tile } from "@carbon/react";

export default function PacientesPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Episodios: acceso individual bloqueado</h1>
        <p className="text-sm text-neutral-700">
          ICEA/ICEA+ se muestra en shadow mode como analítica agregada, no como score operativo por paciente o episodio.
        </p>
      </div>

      <InlineNotification
        kind="warning"
        lowContrast
        title="Uso no individual y no punitivo"
        subtitle="No se muestran scores, predicciones, SHAP ni contribuciones por paciente, episodio, enfermero o turno."
      />

      <Tile className="p-4 rounded-icea space-y-3">
        <h2 className="text-lg font-semibold">Estado de gobernanza</h2>
        <dl className="grid gap-3 text-sm md:grid-cols-2">
          <div>
            <dt className="text-neutral-600">Modo</dt>
            <dd className="font-medium">shadow_only</dd>
          </div>
          <div>
            <dt className="text-neutral-600">Nivel permitido</dt>
            <dd className="font-medium">agregado con supresión por bajo soporte</dd>
          </div>
          <div>
            <dt className="text-neutral-600">Score individual</dt>
            <dd className="font-medium">suprimido</dd>
          </div>
          <div>
            <dt className="text-neutral-600">Interpretación causal individual</dt>
            <dd className="font-medium">no permitida</dd>
          </div>
        </dl>
      </Tile>
    </div>
  );
}
