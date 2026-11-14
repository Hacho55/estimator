// Login de demostración contra el backend de negocio (capa 1 y 2 de los tiers).
// El tier vive en el backend y llega firmado en el token; el front nunca lo elige en el cuerpo.
import type { Tier } from "@/lib/session";

export type DemoUser = { id: string; name: string; tier: Tier };
export type Login = { token: string; expires_at: number; user: DemoUser };

export function businessBase(): string {
  return process.env.NEXT_PUBLIC_BUSINESS_URL ?? "http://127.0.0.1:8100";
}

async function parse<T>(response: Response): Promise<T> {
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error((body as { detail?: string }).detail || `Error ${response.status}`);
  return body as T;
}

export async function listUsers(): Promise<DemoUser[]> {
  return parse(await fetch(`${businessBase()}/api/users`));
}

export async function login(userId: string): Promise<Login> {
  return parse(
    await fetch(`${businessBase()}/api/auth/demo-login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: userId }),
    }),
  );
}

export async function changeTier(token: string, tier: Tier): Promise<Login> {
  return parse(
    await fetch(`${businessBase()}/api/me/tier`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify({ tier }),
    }),
  );
}
