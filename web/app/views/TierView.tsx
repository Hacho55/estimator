"use client";

import type { TierEstimate } from "@/lib/session";
import { DeveloperView } from "./DeveloperView";
import { ExecutiveView } from "./ExecutiveView";
import { PmView } from "./PmView";

// Una vista por tier: el perfil cambia la estructura, no solo el tono.
export function TierView({ view }: { view: TierEstimate }) {
  if (!view.is_estimable) {
    return (
      <article className="estimate">
        <h2>No estimable</h2>
        <p>{view.not_estimable_reason || "La petición no describe un proyecto de software."}</p>
      </article>
    );
  }
  if (view.tier === "developer") return <DeveloperView view={view} />;
  if (view.tier === "pm") return <PmView view={view} />;
  return <ExecutiveView view={view} />;
}
