// Calls to /api/v1 with the browser's session: JSON in and out, the CSRF token on every change, and errors as
// ApiError built from the RFC 9457 problem details the API answers with.

import type { Envelope } from "./types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
    readonly requestId?: string,
    readonly retryAfter?: number,
  ) {
    super(detail);
  }
}

let csrfToken = "";
export const setCsrfToken = (token: string) => {
  csrfToken = token;
};

type Method = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

export async function call<T>(method: Method, path: string, body?: unknown): Promise<Envelope<T>> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET" && csrfToken) headers["X-CSRF-Token"] = csrfToken;
  let response: Response;
  try {
    response = await fetch(`/api/v1${path}`, {
      method,
      headers,
      credentials: "same-origin",
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "The panel cannot be reached. Check the connection and try again.");
  }
  if (response.status === 204) return { data: undefined as T };
  const json = await response.json().catch(() => ({}));
  if (!response.ok) {
    const retry = Number(response.headers.get("Retry-After")) || undefined;
    throw new ApiError(response.status, json.detail ?? response.statusText, json.request_id, retry);
  }
  return json as Envelope<T>;
}

export const api = {
  get: <T>(path: string) => call<T>("GET", path).then((r) => r.data),
  page: <T>(path: string) => call<T>("GET", path),
  post: <T>(path: string, body: unknown = {}) => call<T>("POST", path, body).then((r) => r.data),
  put: <T>(path: string, body: unknown = {}) => call<T>("PUT", path, body).then((r) => r.data),
  patch: <T>(path: string, body: unknown) => call<T>("PATCH", path, body).then((r) => r.data),
  del: <T = void>(path: string, body?: unknown) => call<T>("DELETE", path, body).then((r) => r.data),
};

export const errorText = (error: unknown) =>
  error instanceof ApiError ? error.detail : error instanceof Error ? error.message : "Something went wrong.";
