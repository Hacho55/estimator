"use client";

import type { ReviewTrace } from "@/lib/session";

const DECISION = { accept: "aceptada", iterate: "iterada", synthesize: "sintetizada con avisos" } as const;

// Auditoría Actor-Critic-Boss: qué dijo el crítico en cada vuelta y qué quedó abierto.
export function ReviewPanel({ review }: { review: ReviewTrace }) {
  return (
    <details className="review">
      <summary>
        Revisada por un auditor: {DECISION[review.decision]} · {review.actor_calls} intento
        {review.actor_calls > 1 ? "s" : ""} · {review.open_issues.length} aviso{review.open_issues.length === 1 ? "" : "s"}
      </summary>
      {review.iterations.map((round) => (
        <div key={round.iteration}>
          <p className="meta">
            Vuelta {round.iteration}: {round.verdict} → {round.decision}
            {round.critic_ok ? "" : " (auditor no disponible)"}
          </p>
          <ul>
            {round.issues.map((issue, index) => (
              <li key={`${round.iteration}-${index}`}>
                [{issue.severity}] {issue.category} en {issue.field_path}: {issue.description}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </details>
  );
}
