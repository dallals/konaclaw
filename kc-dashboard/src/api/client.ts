let baseUrl = import.meta.env.VITE_KC_SUPERVISOR_URL ?? "http://127.0.0.1:8765";

export function setBaseUrl(url: string) { baseUrl = url; }
export function getBaseUrl() { return baseUrl; }

async function safeFetch(path: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(`${baseUrl}${path}`, init);
  } catch (e) {
    // fetch() throws TypeError on network failure (connection refused, DNS,
    // CORS rejection). Browsers word this differently — "Load failed" in
    // WebKit, "Failed to fetch" in Chromium, "NetworkError..." in Firefox —
    // so translate every throw from fetch into something actionable.
    const cause = e instanceof Error ? e.message : String(e);
    throw new Error(`Cannot reach KonaClaw supervisor at ${baseUrl} — is it running? (${cause})`);
  }
}

export async function apiGet<T>(path: string): Promise<T> {
  const r = await safeFetch(path);
  if (!r.ok) throw new Error(`GET ${path} → ${r.status}: ${await r.text()}`);
  return r.json();
}

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const r = await safeFetch(path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`POST ${path} → ${r.status}: ${await r.text()}`);
  return r.json();
}

export async function apiPatch<T>(path: string, body: unknown): Promise<T> {
  const r = await safeFetch(path, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`PATCH ${path} → ${r.status}: ${await r.text()}`);
  return r.json();
}

export async function apiDelete(path: string): Promise<void> {
  const r = await safeFetch(path, { method: "DELETE" });
  if (!r.ok) throw new Error(`DELETE ${path} → ${r.status}: ${await r.text()}`);
}
