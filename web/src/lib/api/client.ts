/**
 * Browser-side engine client.
 *
 * Every call goes to this app's own `/api/engine` proxy, which forwards to the
 * FastAPI engine and attaches the operator key server-side. The browser never
 * holds a secret and never makes a cross-origin request.
 *
 * Errors always arrive as the engine's `{ code, message, request_id }`
 * envelope, so `ApiError` carries a stable `code` to switch on and a `message`
 * that is already safe to display.
 */

import type {
  ApiErrorBody,
  CustomerOut,
  OpenCallResponse,
  PersonaOut,
  ReadinessResponse,
  SessionHistoryResponse,
  TelemetryResponse,
  TurnResponse,
} from "./types";

const BASE = "/api/engine";

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly requestId: string | null;

  constructor(code: string, message: string, status: number, requestId: string | null) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.requestId = requestId;
  }

  /** True when retrying the same request could plausibly succeed. */
  get isRetryable(): boolean {
    return (
      this.status >= 500 || this.code === "provider_rate_limited" || this.code === "rate_limited"
    );
  }
}

async function toApiError(response: Response): Promise<ApiError> {
  const requestId = response.headers.get("x-request-id");
  try {
    const body = (await response.json()) as Partial<ApiErrorBody>;
    return new ApiError(
      body.code ?? "unknown_error",
      body.message ?? "Something went wrong.",
      response.status,
      body.request_id ?? requestId,
    );
  } catch {
    return new ApiError(
      "unreadable_error",
      `Request failed with status ${response.status}.`,
      response.status,
      requestId,
    );
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, { ...init, cache: "no-store" });
  } catch (error) {
    throw new ApiError(
      "network_error",
      error instanceof Error && error.message
        ? `Could not reach the server (${error.message}).`
        : "Could not reach the server.",
      0,
      null,
    );
  }
  if (!response.ok) throw await toApiError(response);
  return (await response.json()) as T;
}

// ----------------------------------------------------------------------------
// Calls — the customer-facing line
// ----------------------------------------------------------------------------
export function listPersonas(): Promise<PersonaOut[]> {
  return request<PersonaOut[]>("/calls/personas");
}

/** Starts a call: the agent speaks first and the demo assigns the caller. */
export function openCall(persona: string): Promise<OpenCallResponse> {
  const form = new FormData();
  form.append("persona", persona);
  return request<OpenCallResponse>("/calls/open", { method: "POST", body: form });
}

export interface RunTurnInput {
  /** The assigned caller's number; omitted for a prospect with no account. */
  phoneNumber?: string | null;
  persona: string;
  sessionId?: string | null;
  text?: string;
  audio?: Blob;
}

export function runTurn({
  phoneNumber,
  persona,
  sessionId,
  text,
  audio,
}: RunTurnInput): Promise<TurnResponse> {
  const form = new FormData();
  if (phoneNumber) form.append("phone_number", phoneNumber);
  form.append("persona", persona);
  if (sessionId) form.append("session_id", sessionId);
  if (text) form.append("text", text);
  if (audio) form.append("audio", audio, "turn.wav");

  // No explicit Content-Type: the browser must set the multipart boundary.
  return request<TurnResponse>("/calls/turn", { method: "POST", body: form });
}

export function getSessionHistory(sessionId: string): Promise<SessionHistoryResponse> {
  return request<SessionHistoryResponse>(`/calls/sessions/${encodeURIComponent(sessionId)}`);
}

export async function endSession(sessionId: string): Promise<void> {
  await request(`/calls/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" });
}

// ----------------------------------------------------------------------------
// Operator — proxied with the server-side key
// ----------------------------------------------------------------------------
export function listCustomers(): Promise<CustomerOut[]> {
  return request<CustomerOut[]>("/customers");
}

export function updateCustomerField(
  phoneNumber: string,
  field: string,
  value: unknown,
): Promise<CustomerOut> {
  return request<CustomerOut>(`/customers/${encodeURIComponent(phoneNumber)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ field, value }),
  });
}

export function getTelemetry(persona?: string): Promise<TelemetryResponse> {
  const query = persona ? `?persona=${encodeURIComponent(persona)}` : "";
  return request<TelemetryResponse>(`/telemetry${query}`);
}

export function getReadiness(): Promise<ReadinessResponse> {
  return request<ReadinessResponse>("/readyz");
}

// ----------------------------------------------------------------------------
// Query keys — one place, so invalidation can never typo a key
// ----------------------------------------------------------------------------
export const queryKeys = {
  personas: ["personas"] as const,
  customers: ["customers"] as const,
  readiness: ["readiness"] as const,
  telemetry: (persona?: string) => ["telemetry", persona ?? "all"] as const,
  sessionHistory: (sessionId: string) => ["session-history", sessionId] as const,
};
