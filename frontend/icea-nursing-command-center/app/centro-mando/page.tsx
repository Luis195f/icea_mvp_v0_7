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
          Shadow mode agregado para estructura, proceso y resultado (Donabedian), con case-mix y supresión por bajo soporte.
        </p>
      </div>

      <DashboardSummaryClient />

      <div className="grid gap-4 md:grid-cols-3">
        <MetricCard
          title="Riesgo clínico (on-demand)"
          value="Suprimido"
          subtitle="No se expone score operativo por paciente/episodio."
          status={<StatusPill kind="warning" label="Shadow mode" />}
        />
        <MetricCard
          title="Déficit de dotación proyectado"
          value="—"
          subtitle="Solo monitorización agregada con umbrales mínimos."
          status={<StatusPill kind="warning" label="Pendiente de datos" />}
        />
        <MetricCard
          title="Ahorro económico estimado"
          value="—"
          subtitle="No accionable sin validación, case-mix y revisión de gobernanza."
          status={<StatusPill kind="neutral" label="Piloto" />}
        />
      </div>

      <Tile className="p-4 rounded-icea">
        <div className="flex items-center justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold">Tendencia operativa (demo)</h2>
            <p className="text-sm text-neutral-700">
              Ejemplo de telemetría longitudinal agregada; no permite drill-down individual ni ranking laboral.
            </p>
          </div>
          <StatusPill kind="ok" label="Agregado" />
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
