/** Typed API client. Every dashboard number flows through here from the gateway. */
const TOKEN_KEY = "nanogate.session";

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message);
  }
}

export function getToken(): string | null {
  try {
    return sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(t: string | null) {
  try {
    if (t) sessionStorage.setItem(TOKEN_KEY, t);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable: session lives only in memory for this tab */
  }
}

export async function api<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  let body = init.body;
  if (init.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(init.json);
  }
  const res = await fetch(path, { ...init, headers: { ...headers, ...(init.headers as object) }, body });
  const text = await res.text();
  let data: any = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    // a proxy or crashed handler answered with a plain-text/HTML body: report the HTTP status, not a SyntaxError
    if (res.ok) throw new ApiError(res.status, "BAD_RESPONSE", "The server returned a response that is not JSON");
  }
  if (!res.ok) {
    const d = data?.detail ?? data?.error ?? {};
    if (res.status === 401) setToken(null);
    throw new ApiError(res.status, d.code ?? String(res.status), d.message ?? res.statusText);
  }
  return data as T;
}

export const get = <T>(p: string) => api<T>(p);
export const post = <T>(p: string, json?: unknown) => api<T>(p, { method: "POST", json: json ?? {} });
