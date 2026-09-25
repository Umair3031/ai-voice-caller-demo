/**
 * Backend-for-frontend proxy.
 *
 * Every engine call the browser makes goes through here. Two reasons:
 *
 *   1. The operator API key is a server secret. Operator endpoints serve caller
 *      names, phone numbers, balances, and full transcripts, so the key can
 *      never be shipped in a browser bundle. This route attaches it on the
 *      server, to operator paths only.
 *   2. With no direct browser -> engine traffic there is no cross-origin
 *      surface at all, so CORS needs no configuration and cannot be
 *      misconfigured.
 *
 * The proxy is deliberately dumb: it forwards method, body, and content type
 * unchanged and returns the engine's status and body as-is, so error envelopes
 * and status codes reach the client exactly as the engine wrote them.
 */

import { type NextRequest, NextResponse } from "next/server";

const ENGINE_BASE_URL = process.env.ENGINE_BASE_URL ?? "http://localhost:8000";
const OPERATOR_API_KEY = process.env.OPERATOR_API_KEY ?? "";

/** Paths whose engine routes require `X-API-Key`. */
const OPERATOR_PREFIXES = ["customers", "telemetry"] as const;

/** `GET /calls/sessions/{id}` is operator-only; `DELETE` on it is public, so a
 *  caller can always end their own call. */
function requiresOperatorKey(segments: string[], method: string): boolean {
  const [first, second] = segments;
  if (first && OPERATOR_PREFIXES.includes(first as (typeof OPERATOR_PREFIXES)[number])) {
    return true;
  }
  return first === "calls" && second === "sessions" && method === "GET";
}

/** Hop-by-hop and length headers must not be copied onto a new request. */
const STRIPPED_REQUEST_HEADERS = new Set([
  "host",
  "connection",
  "content-length",
  "transfer-encoding",
  "accept-encoding",
]);

async function proxy(request: NextRequest, segments: string[]): Promise<NextResponse> {
  const target = new URL(
    `/api/v1/${segments.map(encodeURIComponent).join("/")}`,
    ENGINE_BASE_URL,
  );
  target.search = request.nextUrl.search;

  const headers = new Headers();
  request.headers.forEach((value, key) => {
    if (!STRIPPED_REQUEST_HEADERS.has(key.toLowerCase())) headers.set(key, value);
  });
  // Never let a browser supply its own operator key through the proxy.
  headers.delete("x-api-key");

  if (requiresOperatorKey(segments, request.method)) {
    if (!OPERATOR_API_KEY) {
      return NextResponse.json(
        {
          code: "operator_key_not_configured",
          message:
            "This deployment has no OPERATOR_API_KEY set, so operator data is unavailable.",
          request_id: null,
        },
        { status: 503 },
      );
    }
    headers.set("X-API-Key", OPERATOR_API_KEY);
  }

  const hasBody = request.method !== "GET" && request.method !== "DELETE";

  let engineResponse: Response;
  try {
    engineResponse = await fetch(target, {
      method: request.method,
      headers,
      body: hasBody ? await request.arrayBuffer() : undefined,
      cache: "no-store",
      // A turn is three sequential provider round-trips; give it room.
      signal: AbortSignal.timeout(60_000),
    });
  } catch (error) {
    const timedOut = error instanceof DOMException && error.name === "TimeoutError";
    return NextResponse.json(
      {
        code: timedOut ? "engine_timeout" : "engine_unreachable",
        message: timedOut
          ? "The engine took too long to answer. Please try again."
          : "Could not reach the IVR engine. Is it running?",
        request_id: null,
      },
      { status: timedOut ? 504 : 502 },
    );
  }

  const responseHeaders = new Headers();
  const contentType = engineResponse.headers.get("content-type");
  if (contentType) responseHeaders.set("content-type", contentType);
  const requestId = engineResponse.headers.get("x-request-id");
  if (requestId) responseHeaders.set("x-request-id", requestId);
  // The persona roster changes on deploy, not per request. Let Vercel's CDN
  // serve it (and keep serving a stale copy while it refreshes), so the agent
  // arc appears instantly even when the engine is asleep. Everything else is
  // per-caller and stays uncached.
  const isRoster =
    request.method === "GET" &&
    engineResponse.ok &&
    segments.join("/") === "calls/personas";
  responseHeaders.set(
    "cache-control",
    isRoster ? "public, s-maxage=300, stale-while-revalidate=86400" : "no-store",
  );

  return new NextResponse(engineResponse.body, {
    status: engineResponse.status,
    headers: responseHeaders,
  });
}

type RouteContext = { params: Promise<{ path: string[] }> };

async function handler(request: NextRequest, context: RouteContext): Promise<NextResponse> {
  const { path } = await context.params;
  return proxy(request, path);
}

export const GET = handler;
export const POST = handler;
export const PATCH = handler;
export const DELETE = handler;

// Proxied traffic is per-request and never cacheable.
export const dynamic = "force-dynamic";

// A turn is three sequential provider round-trips (STT, LLM, TTS); the
// upstream fetch above already allows 60s for that. Without this, Vercel's
// default function duration can cut a slow turn off before the engine
// answers, well before that 60s budget is used. Raise the platform's own
// deployment setting too if turns still time out under load.
export const maxDuration = 60;
