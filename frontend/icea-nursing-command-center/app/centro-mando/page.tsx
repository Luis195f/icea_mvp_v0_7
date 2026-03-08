import { Tile, Accordion, AccordionItem } from "@carbon/react";
import MetricCard from "@/components/cards/MetricCard";
import StatusPill from "@/components/cards/StatusPill";
import TrendLineChart from "@/components/charts/TrendLineChart";
import { DashboardSummaryClient } from "@/components/pages/DashboardSummaryClient";

export default function CentroMandoPage() {
  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold">Centro de mando de Enfermería (ICEA+)</h1>
        <p className="text-sm text-neutral-700">
          Vista operacional para alta agudeza: **estructura**, **proceso** y **resultado** (Donabedian).
        </p>
      </div>

      <DashboardSummaryClient />

      <div className="grid gap-4 md:grid-cols-3">
        <MetricCard
          title="Riesgo clínico (on-demand)"
          value="Interactivo"
          subtitle="Predicción conformal + explicación tipo SHAP en «Pacientes»."
          status={<StatusPill kind="info" label="Acción clínica" />}
        />
        <MetricCard
          title="Déficit de dotación proyectado"
          value="—"
          subtitle="Cargar roster CSV para activar proyección por turno."
          status={<StatusPill kind="warning" label="Pendiente de datos" />}
        />
        <MetricCard
          title="Ahorro económico estimado"
          value="—"
          subtitle="Se habilita con pipeline + modelos calibrados en producción."
          status={<StatusPill kind="neutral" label="Piloto" />}
        />
      </div>

      <Tile className="p-4 rounded-icea">
        <div className="flex items-center justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold">Tendencia operativa (demo)</h2>
            <p className="text-sm text-neutral-700">
              Ejemplo de telemetría longitudinal. Sustituir por KPIs reales (LOS, readmisiones, boarding, etc.).
            </p>
          </div>
          <StatusPill kind="ok" label="Comprensión en 5 segundos" />
        </div>
        <div className="mt-4">
          <TrendLineChart />
        </div>
      </Tile>

      <Accordion>
        <AccordionItem title="Principios de UX clínica (carga cognitiva)">
          <ul className="list-disc pl-6 text-sm text-neutral-800 space-y-1">
            <li>Regla 7±2: solo KPIs críticos en primera vista.</li>
            <li>Divulgación progresiva: detalles bajo interacción.</li>
            <li>Accesibilidad: nunca depender solo de color; iconos y texto siempre.</li>
            <li>Seguridad: el navegador no firma; el BFF aplica HMAC + anti-replay.</li>
          </ul>
        </AccordionItem>
      </Accordion>
    </div>
  );
}
