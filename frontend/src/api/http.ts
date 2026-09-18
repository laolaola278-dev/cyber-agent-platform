import axios from "axios";

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? "/api",
  timeout: 10000,
  withCredentials: true,
});

export default api;

/**
 * The platform answers failures in three different shapes, and the console has
 * to read all of them:
 *
 *  1. `{"detail": "..."}`            -- FastAPI HTTPException (auth, 404s).
 *  2. `{"detail": [{loc, msg}]}`     -- request-body validation errors.
 *  3. `{"error": {code, message}}`   -- the domain envelope from the platform
 *     exception handlers (SECRET_NOT_FOUND, AssetConflict, ...).
 *
 * Reading only (1) meant every domain failure showed the operator axios's own
 * "Request failed with status code 404" and every validation failure showed
 * "[object Object]" instead of the reason the platform already gave.
 */
interface PlatformError {
  code?: string;
  message?: string;
}

interface ValidationDetail {
  loc?: unknown;
  msg?: string;
}

const fieldPath = (detail: ValidationDetail): string => {
  const loc = Array.isArray(detail.loc) ? detail.loc.slice(1) : [];
  return loc.map(String).join(".");
};

export const errorMessage = (error: unknown, fallback: string): string => {
  if (!axios.isAxiosError(error)) {
    return error instanceof Error && error.message ? error.message : fallback;
  }

  const body = error.response?.data as
    | { detail?: unknown; error?: PlatformError }
    | undefined;

  const platform = body?.error;
  if (platform?.message) {
    return platform.code ? `${platform.message}（${platform.code}）` : platform.message;
  }

  const detail = body?.detail;
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    // Report every offending field: a form with two invalid inputs that names
    // one of them still leaves the operator guessing.
    const messages = (detail as ValidationDetail[])
      .map((item) => {
        const path = fieldPath(item);
        const message = item.msg ?? "";
        return path && message ? `${path}: ${message}` : path || message;
      })
      .filter(Boolean);
    if (messages.length > 0) return messages.join("；");
  }

  return error.message || fallback;
};
