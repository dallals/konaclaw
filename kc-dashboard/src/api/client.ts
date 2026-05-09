let baseUrl = import.meta.env.VITE_KC_SUPERVISOR_URL ?? "http://127.0.0.1:8765";

export function setBaseUrl(url: string) { baseUrl = url; }
export function getBaseUrl() { return baseUrl; }

export async function apiGet<T>(path: string): Promise<T> {
  const r = await fetch(`${baseUrl}${path}`);
  if (!r.ok) throw new Error(`GET ${path} → ${r.status}: ${await r.text()}`);
  return r.json();
}

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(`${baseUrl}${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`POST ${path} → ${r.status}: ${await r.text()}`);
  return r.json();
}

export async function apiPatch<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(`${baseUrl}${path}`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`PATCH ${path} → ${r.status}: ${await r.text()}`);
  return r.json();
}

export async function apiDelete(path: string): Promise<void> {
  const r = await fetch(`${baseUrl}${path}`, { method: "DELETE" });
  if (!r.ok) throw new Error(`DELETE ${path} → ${r.status}: ${await r.text()}`);
}
