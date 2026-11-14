"use client";

import { useEffect, useState } from "react";
import { changeTier, listUsers, login, type DemoUser, type Login } from "@/lib/auth";
import type { Tier } from "@/lib/session";

const TIERS: { value: Tier; label: string }[] = [
  { value: "developer", label: "Ingeniería" },
  { value: "pm", label: "Entrega (PM)" },
  { value: "executive", label: "Dirección" },
];

// Login de demostración contra el backend de negocio. Sin backend, el estimador usa su tier por defecto.
export function UserBar({ auth, onAuth }: { auth: Login | null; onAuth: (value: Login | null) => void }) {
  const [users, setUsers] = useState<DemoUser[]>([]);
  const [offline, setOffline] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    listUsers()
      .then(setUsers)
      .catch(() => setOffline(true));
  }, []);

  async function choose(userId: string) {
    setError("");
    if (!userId) return onAuth(null);
    try {
      onAuth(await login(userId));
    } catch {
      setError("No se pudo iniciar sesión.");
    }
  }

  async function switchTier(tier: Tier) {
    if (!auth) return;
    try {
      onAuth(await changeTier(auth.token, tier));
    } catch {
      setError("No se pudo cambiar el perfil.");
    }
  }

  if (offline) {
    return <p className="meta userbar">Backend de negocio no disponible: perfil por defecto del estimador.</p>;
  }
  return (
    <div className="userbar">
      <label>
        Usuario
        <select value={auth?.user.id ?? ""} onChange={(event) => choose(event.target.value)}>
          <option value="">Elegir usuario</option>
          {users.map((user) => (
            <option key={user.id} value={user.id}>
              {user.name}
            </option>
          ))}
        </select>
      </label>
      {auth ? (
        <label>
          Perfil
          <select value={auth.user.tier} onChange={(event) => switchTier(event.target.value as Tier)}>
            {TIERS.map((tier) => (
              <option key={tier.value} value={tier.value}>
                {tier.label}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      {error ? <p className="error">{error}</p> : null}
    </div>
  );
}
