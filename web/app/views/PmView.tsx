"use client";

import type { PmEstimate } from "@/lib/session";
import { euros, range } from "./format";

// Entrega: fases, hitos con su coste acumulado, equipo, duración y bloqueos.
export function PmView({ view }: { view: PmEstimate }) {
  return (
    <article className="estimate" data-tier="pm">
      <h2>{view.project_title}</h2>
      <p>{view.summary}</p>
      <p className="totals">
        {euros(view.total_cost_eur)} · {range(view.duration_weeks_range.min, view.duration_weeks_range.max, "semanas")}
      </p>
      <table>
        <thead>
          <tr>
            <th>Fase</th>
            <th>Semanas</th>
            <th>Horas</th>
            <th>Coste</th>
          </tr>
        </thead>
        <tbody>
          {view.phases.map((phase) => (
            <tr key={phase.name}>
              <td>{phase.name}</td>
              <td>{phase.weeks}</td>
              <td>{phase.hours}</td>
              <td>{euros(phase.cost_eur)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <h3>Hitos</h3>
      <ul>
        {view.milestones.map((item) => (
          <li key={item.name}>
            <strong>{item.name}</strong> ({item.phase}): {item.deliverable} · acumulado {euros(item.cumulative_cost_eur)}
          </li>
        ))}
      </ul>
      <h3>Equipo</h3>
      <ul>
        {view.team_composition.map((member) => (
          <li key={member.role}>
            {member.count} {member.role} (dedicación {member.dedication})
          </li>
        ))}
      </ul>
      {view.blockers.length ? (
        <>
          <h3>Bloqueos</h3>
          <ul>
            {view.blockers.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </>
      ) : null}
      <h3>Riesgos</h3>
      <ul>
        {view.risks.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </article>
  );
}
