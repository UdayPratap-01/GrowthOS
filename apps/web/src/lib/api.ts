const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000/api/v1";

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

function getToken() {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("growthos_access_token");
}

export function setTokens(access: string, refresh: string) {
  localStorage.setItem("growthos_access_token", access);
  localStorage.setItem("growthos_refresh_token", refresh);
}

export function clearTokens() {
  localStorage.removeItem("growthos_access_token");
  localStorage.removeItem("growthos_refresh_token");
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers || {});
  if (!headers.has("Content-Type") && options.body) {
    headers.set("Content-Type", "application/json");
  }
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const res = await fetch(`${API_URL}${path}`, { ...options, headers });
  if (!res.ok) {
    let detail = "Request failed";
    try {
      const data = await res.json();
      detail = data.detail || detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(typeof detail === "string" ? detail : JSON.stringify(detail), res.status);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}
