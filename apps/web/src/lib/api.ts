const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000/api/v1";

/**
 * A failed API call.
 *
 * `code` is the stable identifier to branch on; `message` is display text and
 * may be reworded server-side. `requestId` is shown to the user on unexpected
 * failures so support can find the corresponding server-side stack trace.
 */
export class ApiError extends Error {
  status: number;
  code: string;
  requestId?: string;
  fields?: { field: string; message: string }[];

  constructor(
    message: string,
    status: number,
    options: { code?: string; requestId?: string; fields?: { field: string; message: string }[] } = {},
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = options.code || codeForStatus(status);
    this.requestId = options.requestId;
    this.fields = options.fields;
  }

  /** Retrying the same request could plausibly succeed. */
  get isRetryable() {
    return this.status >= 500 || this.status === 429;
  }

  /**
   * The plan does not cover this, so retrying will not help and the UI should
   * offer an upgrade rather than a retry button.
   */
  get isPlanLimit() {
    return this.status === 402;
  }
}

function codeForStatus(status: number) {
  if (status === 401) return "UNAUTHENTICATED";
  if (status === 402) return "QUOTA_EXCEEDED";
  if (status === 403) return "PERMISSION_DENIED";
  if (status === 404) return "NOT_FOUND";
  if (status === 429) return "RATE_LIMITED";
  if (status >= 500) return "INTERNAL_ERROR";
  return "REQUEST_FAILED";
}

/** Wording for codes where the server's message is not the best thing to show. */
const MESSAGES: Record<string, string> = {
  RATE_LIMITED: "Too many requests. Please wait a moment and try again.",
  AI_GENERATION_FAILED: "The AI provider could not complete this request. Nothing was saved.",
  STORAGE_UNAVAILABLE: "File storage is temporarily unavailable. Your asset was not lost.",
  DATABASE_UNAVAILABLE: "The service is temporarily unavailable. Please try again shortly.",
  CONFIGURATION_ERROR: "This feature is not configured. Contact your administrator.",
  PERMISSION_DENIED: "You do not have permission to perform this action.",
};

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return MESSAGES[error.code] || error.message;
  }
  return error instanceof Error ? error.message : "Something went wrong.";
}

function getToken() {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("growthos_access_token");
}

/**
 * Store the short-lived access token.
 *
 * The refresh token is deliberately absent: the API delivers it as an httpOnly
 * cookie, so it is never readable by page scripts. Anything we put in
 * localStorage is readable by any injected script, which would turn a single
 * XSS into a durable session.
 */
export function setTokens(access: string) {
  localStorage.setItem("growthos_access_token", access);
}

export function clearTokens() {
  localStorage.removeItem("growthos_access_token");
  // Older builds kept a refresh token here. Remove it on the way past so an
  // upgraded browser does not keep carrying one around.
  localStorage.removeItem("growthos_refresh_token");
}

/**
 * Sign out.
 *
 * Clearing local storage only hides the token; the session stays valid until it
 * expires. This tells the server to revoke it, and still clears locally even if
 * that call fails, so the user is never stuck signed in.
 */
export async function logout() {
  try {
    await fetch(`${API_URL}/auth/logout`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // The refresh cookie identifies the session to revoke.
      credentials: "include",
      body: "{}",
    });
  } catch {
    /* offline sign-out is still a sign-out locally */
  }
  clearTokens();
}

let redirectingToLogin = false;

function redirectToLogin() {
  if (typeof window === "undefined") return;
  if (window.location.pathname.startsWith("/login")) return;
  if (redirectingToLogin) return;
  redirectingToLogin = true;
  clearTokens();
  const next = encodeURIComponent(window.location.pathname + window.location.search);
  window.location.href = `/login?next=${next}`;
}

type ErrorEnvelope = {
  error?: { code?: string; message?: string; request_id?: string; fields?: { field: string; message: string }[] };
  detail?: unknown;
};

async function buildError(res: Response): Promise<ApiError> {
  let message = "Request failed";
  let envelope: ErrorEnvelope["error"];

  try {
    const data = (await res.json()) as ErrorEnvelope;
    envelope = data.error;
    // `detail` is the legacy shape; still read it so an older API keeps working.
    const fallback = data.detail;
    message =
      envelope?.message ||
      (typeof fallback === "string" ? fallback : fallback ? JSON.stringify(fallback) : message);
  } catch {
    // A proxy timeout or gateway error returns HTML, not JSON.
    message = res.status >= 500 ? "The server is not responding." : message;
  }

  return new ApiError(message, res.status, {
    code: envelope?.code,
    requestId: envelope?.request_id || res.headers.get("X-Request-ID") || undefined,
    fields: envelope?.fields,
  });
}

/**
 * Exchange the httpOnly refresh cookie for a new access token.
 *
 * Access tokens expire after an hour. Without this the user is thrown back to
 * the login screen mid-session, which is what the audit observed. Concurrent
 * 401s share one in-flight refresh so a page with several parallel requests
 * does not rotate the token several times — with rotation enabled server-side,
 * that would look like token reuse and revoke the whole session.
 */
let refreshInFlight: Promise<boolean> | null = null;

async function refreshSession(): Promise<boolean> {
  if (typeof window === "undefined") return false;
  if (!refreshInFlight) {
    refreshInFlight = (async () => {
      try {
        const res = await fetch(`${API_URL}/auth/refresh`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          // The rotated refresh token comes back as a cookie, not in the body.
          credentials: "include",
          body: "{}",
        });
        if (!res.ok) return false;
        const data = (await res.json()) as { access_token: string };
        setTokens(data.access_token);
        return true;
      } catch {
        return false;
      } finally {
        // Cleared on the next tick so callers awaiting this promise all see it.
        setTimeout(() => {
          refreshInFlight = null;
        }, 0);
      }
    })();
  }
  return refreshInFlight;
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const send = () => {
    const headers = new Headers(options.headers || {});
    if (!headers.has("Content-Type") && options.body) {
      headers.set("Content-Type", "application/json");
    }
    const token = getToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    return fetch(`${API_URL}${path}`, { ...options, headers, credentials: "include" });
  };

  let res = await send();

  if (res.status === 401 && !path.startsWith("/auth/")) {
    if (await refreshSession()) {
      res = await send();
    }
  }

  if (!res.ok) {
    if (res.status === 401) redirectToLogin();
    throw await buildError(res);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

/**
 * Save a creative asset to disk.
 *
 * Routed through the authenticated API with `?download=true` rather than a
 * public link, so an asset can only be saved by someone entitled to read it and
 * the server supplies the filename.
 */
export async function downloadMedia(path: string): Promise<void> {
  const objectUrl = await fetchMediaObjectUrl(
    path.includes("?") ? `${path}&download=true` : `${path}?download=true`,
  );
  try {
    const anchor = document.createElement("a");
    anchor.href = objectUrl;
    // Left empty so the server's Content-Disposition filename is used.
    anchor.download = "";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  } finally {
    // Revoked on a delay: revoking immediately can cancel the download in
    // Safari before it has read the blob.
    setTimeout(() => URL.revokeObjectURL(objectUrl), 10_000);
  }
}

/** Authenticated binary fetch for creative media (never use bare img src without auth). */
export async function fetchMediaObjectUrl(path: string): Promise<string> {
  const token = getToken();
  const headers = new Headers();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const base = API_URL.replace(/\/api\/v1$/, "");
  const normalized = path.startsWith("http")
    ? path
    : path.startsWith("/api/")
      ? `${base}${path}`
      : `${API_URL}${path.startsWith("/") ? path : `/${path}`}`;
  const res = await fetch(normalized, { headers });
  if (!res.ok) {
    if (res.status === 401) redirectToLogin();
    throw new ApiError("Media fetch failed", res.status);
  }
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}
