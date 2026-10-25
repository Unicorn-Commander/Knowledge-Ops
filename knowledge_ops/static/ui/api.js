/* Knowledge-Ops shell — same-origin API client for /kb.* endpoints.
   Behind oauth2-proxy the browser carries the session cookie automatically.
   Contract: api.get(path, params?) / api.post(path, body) -> parsed JSON;
   failures throw KoErr {status, message}. 30s timeout via AbortController. */

const TIMEOUT_MS = 30000;

export class KoErr extends Error {
  constructor(status, message) {
    super(message || `HTTP ${status}`);
    this.name = "KoErr";
    this.status = status;
  }
}

function fallbackMessage(status) {
  if (status === 401) return "Session expired — reload to sign in.";
  if (status === 403) return "Requires knowledge-admin.";
  if (status === 404) return "Not found.";
  if (status >= 500) return "The service hit an internal error.";
  return `HTTP ${status}`;
}

async function fetchResponse(path, opts = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  let res;
  try {
    res = await fetch(path, {
      credentials: "same-origin",
      ...opts,
      signal: controller.signal,
    });
  } catch (e) {
    clearTimeout(timer);
    if (e && e.name === "AbortError") {
      throw new KoErr(0, "Request timed out — the service did not answer within 30 seconds.");
    }
    throw new KoErr(0, "Network error — the service is unreachable.");
  }
  clearTimeout(timer);
  return res;
}

async function request(path, opts = {}) {
  const res = await fetchResponse(path, {
    ...opts,
    headers: {
      Accept: "application/json",
      ...(opts.body ? { "Content-Type": "application/json" } : {}),
      ...(opts.headers || {}),
    },
  });

  const ct = (res.headers.get("content-type") || "").toLowerCase();
  let body = null;
  let parsed = false;
  if (ct.includes("application/json")) {
    try { body = await res.json(); parsed = true; } catch { body = null; }
  } else if (ct.includes("image/svg")) {
    // kb.get_metric_chart returns SVG markup; pass it through as text.
    body = await res.text();
    parsed = true;
  }

  if (!res.ok) {
    const raw = body && (body.detail ?? body.error ?? body.message);
    const message = typeof raw === "string"
      ? raw
      : (raw && typeof raw === "object" && (raw.message || raw.detail)) || fallbackMessage(res.status);
    throw new KoErr(res.status, message);
  }

  if (!parsed) {
    // A 200 that is not JSON usually means oauth2-proxy redirected us to its
    // HTML sign-in page — treat as an expired session.
    if (res.redirected || ct.includes("text/html")) {
      throw new KoErr(401, "Session expired — reload to sign in.");
    }
    try { body = await res.json(); } catch {
      throw new KoErr(res.status, "Unexpected non-JSON response.");
    }
  }
  return body;
}

async function ping(path) {
  const res = await fetchResponse(path);
  if (!res.ok) throw new KoErr(res.status);
}

function qs(params) {
  if (!params) return "";
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") u.set(k, String(v));
  }
  const s = u.toString();
  return s ? `?${s}` : "";
}

export const api = {
  get: (path, params) => request(path + qs(params)),
  post: (path, body) => request(path, { method: "POST", body: JSON.stringify(body ?? {}) }),
  ping,
};
