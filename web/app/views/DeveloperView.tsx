"use client";

import type { DeveloperEstimate } from "@/lib/session";
import { euros, range } from "./format";

// Ingeniería: trabajo por componente técnico, riesgos y supuestos de stack, fuentes de incertidumbre.
export function DeveloperView({ view }: { view: DeveloperEstimate }) {
  return (
    <article className="estimate" data-tier="developer">
      <h2>{view.project_title}</h2>
      <p>{view.summary}</p>
      <p className="totals">
        {view.total_hours} h ({range(view.total_hours_range.min, view.total_hours_range.max, "h")}) · {euros(view.total_cost_eur)}
      </p>
      {view.components.map((component) => (
        <section key={component.name}>
          <h3>
            {component.name} · {component.hours} h · {euros(component.cost_eur)}
          </h3>
          <table>
            <thead>
              <tr>
                <th>Trabajo</th>
                <th>Rol</th>
                <th>Horas</th>
                <th>Coste</th>
              </tr>
            </thead>
            <tbody>
              {component.work.map((line) => (
                <tr key={line.name}>
                  <td>{line.name}</td>
                  <td>{line.role}</td>
                  <td>{line.hours}</td>
                  <td>{euros(line.cost_eur)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {component.technical_risks.length ? (
            <p className="phase-notes">Riesgos: {component.technical_risks.join(" · ")}</p>
          ) : null}
          {component.stack_assumptions.length ? (
            <p className="phase-notes">Stack: {component.stack_assumptions.join(" · ")}</p>
          ) : null}
        </section>
      ))}
      {view.uncertainty_drivers.length ? (
        <>
          <h3>Fuentes de incertidumbre (&gt;20 %)</h3>
          <ul>
            {view.uncertainty_drivers.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </>
      ) : null}
      <h3>Supuestos</h3>
      <ul>
        {view.assumptions.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </article>
  );
}
