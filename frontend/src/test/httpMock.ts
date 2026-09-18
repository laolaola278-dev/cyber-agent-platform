import axios, {
  AxiosError,
  type AxiosRequestConfig,
  type AxiosResponse,
  type InternalAxiosRequestConfig,
} from "axios";
import api from "../api/http";

/**
 * Intercepts the console's HTTP boundary at the axios adapter.
 *
 * Deliberately not a module mock: the real `api` instance, its baseURL,
 * interceptors and `errorMessage()` all still run, so a test that asserts on an
 * error message is exercising the code that renders it in production.
 */

export interface Reply {
  status?: number;
  data?: unknown;
  detail?: string;
}

export interface Call {
  method: string;
  url: string;
  config: AxiosRequestConfig;
}

let routes: Record<string, Reply | (() => Reply)> = {};
let strict = true;
const recorded: Call[] = [];

const adapter = async (config: InternalAxiosRequestConfig): Promise<AxiosResponse> => {
  const method = (config.method ?? "get").toLowerCase();
  const url = (config.url ?? "").replace(/^https?:\/\/[^/]+/, "");
  recorded.push({ method, url, config });

  const key = `${method.toUpperCase()} ${url}`;
  const entry = routes[key] ?? (method === "get" ? routes[url] : undefined);

  if (entry === undefined) {
    if (!strict) return { data: {}, status: 200, statusText: "OK", headers: {}, config };
    throw new AxiosError(
      `unstubbed ${key}`, "ERR_BAD_REQUEST", config, {},
      { status: 404, statusText: "Not Found", data: { detail: `unstubbed ${key}` }, headers: {}, config },
    );
  }

  const reply = typeof entry === "function" ? entry() : entry;
  const status = reply.status ?? 200;
  const payload = reply.detail !== undefined ? { detail: reply.detail } : (reply.data ?? {});

  if (status >= 400) {
    throw new AxiosError(
      `Request failed with status code ${status}`,
      "ERR_BAD_RESPONSE", config, {},
      { status, statusText: "Error", data: payload, headers: {}, config },
    );
  }

  return { data: payload, status, statusText: "OK", headers: {}, config };
};

api.defaults.adapter = adapter;

/** Register `METHOD /path` (or bare `/path` for GET) -> reply. */
export function stub(nextRoutes: Record<string, Reply | (() => Reply)>, options: { strict?: boolean } = {}) {
  routes = nextRoutes;
  strict = options.strict ?? true;
}

export function calls(method?: string): Call[] {
  return method ? recorded.filter((call) => call.method === method) : recorded;
}

export function resetCalls(): void {
  recorded.length = 0;
}

export const page = <T>(items: T[], total = items.length) => ({
  data: { items, page: 1, page_size: items.length || 20, total },
});

export { axios };
