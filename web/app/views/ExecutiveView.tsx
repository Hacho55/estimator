"use client";

import type { ExecutiveEstimate } from "@/lib/session";
import { euros, range } from "./format";

const CONFIDENCE = { low: "baja", medium: "media", high: "alta" } as const;

// Dirección: cifras de cabecera, confianza, tres riesgos y recomendación. Sin desglose técnico.
export function ExecutiveView({ view }: { view: ExecutiveEstimate }) {
  return (
    <article className="estimate" data-tier="executive">
      <h2>{view.project_title}</h2>
      <p>{view.summary}</p>
      <dl className="stats headline">
        <div>
          <dt>Coste</dt>
          <dd>
            {euros(view.headline_cost_range.min)} – {euros(view.headline_cost_range.max)}
          </dd>
        </div>
        <div>
          <dt>Duración</dt>
          <dd>{range(view.headline_duration_range.min, view.headline_duration_range.max, "semanas")}</dd>
        </div>
        <div>
          <dt>Confianza</dt>
          <dd>{CONFIDENCE[view.confidence_level]}</dd>
        </div>
      </dl>
      <p className="phase-notes">{view.confidence_reason}</p>
      <h3>Tres riesgos principales</h3>
      <ol>
        {view.top_three_risks.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ol>
      <h3>Recomendación</h3>
      <p>{view.go_no_go_recommendation}</p>
    </article>
  );
}
