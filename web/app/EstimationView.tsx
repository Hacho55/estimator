"use client";

import type { StructuredEstimation } from "@/lib/readEstimateStream";
import type { OutputFormat } from "@/lib/types";

function euros(value: number): string {
  return `${value.toLocaleString("es-ES", { maximumFractionDigits: 0 })} €`;
}

function assumptionsFor(estimation: StructuredEstimation, phase: string): string[] {
  return estimation.phase_assumptions?.find((entry) => entry.phase === phase)?.assumptions ?? [];
}

export function EstimationView({
  estimation,
  format,
}: {
  estimation: StructuredEstimation;
  format: OutputFormat;
}) {
  if (!estimation.is_estimable) {
    return (
      <article className="estimate">
        <h2>No estimable</h2>
        <p>{estimation.not_estimable_reason || "La petición no describe un proyecto de software."}</p>
      </article>
    );
  }

  const phases = estimation.phases ?? [];
  const title = estimation.project_title || "Estimación";

  if (format === "line_items") {
    return (
      <article className="estimate">
        <h2>{title}</h2>
        <p>{estimation.summary}</p>
        <table>
          <thead>
            <tr>
              <th>Tarea</th>
              <th>Fase</th>
              <th>Rol</th>
              <th>Horas</th>
              <th>Tarifa</th>
              <th>Coste</th>
            </tr>
          </thead>
          <tbody>
            {estimation.tasks.map((task) => (
              <tr key={`${task.phase}-${task.name}`}>
                <td>{task.name}</td>
                <td>{task.phase}</td>
                <td>{task.role}</td>
                <td>{task.hours}</td>
                <td>{euros(task.rate_eur)}/h</td>
                <td>{euros(task.cost_eur)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <Totals estimation={estimation} />
        <Lists estimation={estimation} />
      </article>
    );
  }

  if (format === "narrative") {
    return (
      <article className="estimate">
        <h2>{title}</h2>
        <p>{estimation.summary}</p>
        {phases.map((phase) => {
          const tasks = estimation.tasks.filter((task) => task.phase === phase.phase);
          const notes = assumptionsFor(estimation, phase.phase);
          return (
            <section key={phase.phase} className="narrative-phase">
              <h3>{phase.phase}</h3>
              <p>
                Aproximadamente {phase.hours} horas y {euros(phase.cost_eur)}
                {phase.weeks ? ` (~${phase.weeks} semanas)` : ""}.
              </p>
              {tasks.length ? (
                <p>{tasks.map((task) => `${task.name} (${task.hours} h, ${task.role})`).join("; ")}.</p>
              ) : null}
              {notes.length ? (
                <ul>
                  {notes.map((note) => (
                    <li key={note}>{note}</li>
                  ))}
                </ul>
              ) : null}
            </section>
          );
        })}
        <Totals estimation={estimation} />
        <Lists estimation={estimation} showPhaseAssumptions={false} />
      </article>
    );
  }

  return (
    <article className="estimate">
      <h2>{title}</h2>
      <p>{estimation.summary}</p>
      <table>
        <thead>
          <tr>
            <th>Fase</th>
            <th>Horas</th>
            <th>Coste</th>
            <th>Semanas</th>
          </tr>
        </thead>
        <tbody>
          {phases.map((phase) => (
            <tr key={phase.phase}>
              <td>
                {phase.phase}
                {assumptionsFor(estimation, phase.phase).length ? (
                  <ul className="phase-notes">
                    {assumptionsFor(estimation, phase.phase).map((note) => (
                      <li key={note}>{note}</li>
                    ))}
                  </ul>
                ) : null}
              </td>
              <td>{phase.hours}</td>
              <td>{euros(phase.cost_eur)}</td>
              <td>{phase.weeks}</td>
            </tr>
          ))}
          <tr>
            <th>Total</th>
            <th>{estimation.total_hours}</th>
            <th>{euros(estimation.total_cost_eur)}</th>
            <th>
              {estimation.duration_weeks_min}–{estimation.duration_weeks_max}
            </th>
          </tr>
        </tbody>
      </table>
      <Lists estimation={estimation} showPhaseAssumptions={false} />
    </article>
  );
}

function Totals({ estimation }: { estimation: StructuredEstimation }) {
  return (
    <p className="totals">
      Totales: {estimation.total_hours} h · {euros(estimation.total_cost_eur)} ·{" "}
      {estimation.duration_weeks_min}–{estimation.duration_weeks_max} semanas
    </p>
  );
}

function Lists({
  estimation,
  showPhaseAssumptions = true,
}: {
  estimation: StructuredEstimation;
  showPhaseAssumptions?: boolean;
}) {
  return (
    <>
      {estimation.team?.length ? (
        <>
          <h3>Equipo</h3>
          <ul>
            {estimation.team.map((member) => (
              <li key={member.role}>
                {member.count}× {member.role} ({Math.round(member.dedication * 100)}%)
              </li>
            ))}
          </ul>
        </>
      ) : null}
      {estimation.assumptions?.length ? (
        <>
          <h3>Supuestos</h3>
          <ul>
            {estimation.assumptions.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </>
      ) : null}
      {showPhaseAssumptions && estimation.phase_assumptions?.length
        ? estimation.phase_assumptions.map((entry) => (
            <div key={entry.phase}>
              <h3>Supuestos · {entry.phase}</h3>
              <ul>
                {entry.assumptions.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </div>
          ))
        : null}
      {estimation.risks?.length ? (
        <>
          <h3>Riesgos</h3>
          <ul>
            {estimation.risks.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </>
      ) : null}
    </>
  );
}
