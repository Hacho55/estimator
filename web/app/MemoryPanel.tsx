"use client";

import type { MemoryChange, ProjectMetadata } from "@/lib/session";

// La memoria de la sesión (hechos destilados), con el turno y la evidencia de cada cambio.
function origin(log: MemoryChange[], field: string): string | undefined {
  const last = [...log].reverse().find((change) => change.field === field);
  if (!last) return undefined;
  return `turno ${last.turn}${last.evidence ? `: «${last.evidence}»` : ""}`;
}

function Fact({ label, value, title }: { label: string; value: string; title?: string }) {
  return (
    <div title={title}>
      <dt>
        {label}
        {title ? <span className="origin"> · {title.split(":")[0]}</span> : null}
      </dt>
      <dd>{value}</dd>
    </div>
  );
}

export function MemoryPanel({
  sessionId,
  metadata,
  log,
  tier,
}: {
  sessionId: string | null;
  metadata: ProjectMetadata;
  log: MemoryChange[];
  tier: string | null;
}) {
  const list = (items: string[]) => (items.length ? items.join(", ") : "—");
  return (
    <aside className="metadata" aria-label="project_metadata">
      <h2>Memoria del proyecto</h2>
      <p className="meta">{sessionId ? `sesión ${sessionId.slice(0, 8)}${tier ? ` · ${tier}` : ""}` : "sin sesión"}</p>
      <dl>
        <Fact label="Nombre" value={metadata.project_name || "—"} title={origin(log, "project_name")} />
        <Fact label="Equipo asumido" value={String(metadata.assumed_team_size ?? "—")} title={origin(log, "assumed_team_size")} />
        <Fact label="Tecnologías" value={list(metadata.mentioned_technologies)} title={origin(log, "mentioned_technologies")} />
        <Fact label="Alcance acordado" value={metadata.agreed_scope || "—"} title={origin(log, "agreed_scope")} />
        <Fact label="Restricciones" value={list(metadata.explicit_constraints)} title={origin(log, "explicit_constraints")} />
        <Fact label="Descartado" value={list(metadata.rejected_options)} title={origin(log, "rejected_options")} />
      </dl>
    </aside>
  );
}
