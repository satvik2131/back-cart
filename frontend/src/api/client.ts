import type { ErrorEnvelope } from "./types";

const API_BASE = import.meta.env.VITE_API_BASE ?? "/api";

/**
 * Raised for every non-2xx response. `code` / `message` come straight from the
 * backend's error envelope when present, so the UI can show exactly what the
 * server said. `network` is true when the request never got a response at all
 * (offline, timeout, server down) — the checkout retry flow keys off this.
 */
export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly details: Record<string, unknown>;
  readonly network: boolean;

  constructor(opts: {
    code: string;
    message: string;
    status: number;
    details?: Record<string, unknown>;
    network?: boolean;
  }) {
    super(opts.message);
    this.name = "ApiError";
    this.code = opts.code;
    this.status = opts.status;
    this.details = opts.details ?? {};
    this.network = opts.network ?? false;
  }
}

function isErrorEnvelope(body: unknown): body is ErrorEnvelope {
  return (
    typeof body === "object" &&
    body !== null &&
    "error" in body &&
    typeof (body as ErrorEnvelope).error?.code === "string"
  );
}

export interface RequestOptions {
  method?: string;
  body?: unknown;
  headers?: Record<string, string>;
}

/** Single fetch wrapper. Every api/ function goes through this. */
export async function request<T>(
  path: string,
  { method = "GET", body, headers = {} }: RequestOptions = {},
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method,
      headers: {
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
        ...headers,
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError({
      code: "NETWORK_ERROR",
      message:
        "Could not reach the API. It may be down, or the request may have " +
        "been sent without a response.",
      status: 0,
      network: true,
    });
  }

  if (response.status === 204) {
    return undefined as T;
  }

  const text = await response.text();
  const parsed: unknown = text ? JSON.parse(text) : undefined;

  if (!response.ok) {
    if (isErrorEnvelope(parsed)) {
      throw new ApiError({
        code: parsed.error.code,
        message: parsed.error.message,
        status: response.status,
        details: parsed.error.details,
      });
    }
    throw new ApiError({
      code: "HTTP_ERROR",
      message: `Request failed with status ${response.status}.`,
      status: response.status,
    });
  }

  return parsed as T;
}
