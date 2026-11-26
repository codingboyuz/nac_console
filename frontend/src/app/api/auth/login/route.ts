import { NextResponse } from "next/server";
import { BACKEND_URL, forwardHeaders, problem, setTokenCookies, type TokenResponse } from "@/lib/server/backend";

export async function POST(req: Request) {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return problem(400, "Bad Request", "invalid JSON body");
  }
  const headers = forwardHeaders(req);
  headers.set("content-type", "application/json");
  let r: Response;
  try {
    r = await fetch(`${BACKEND_URL}/api/v1/auth/login`, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
      cache: "no-store",
    });
  } catch {
    return problem(503, "Backend Unavailable", "backend is not reachable");
  }
  if (!r.ok) {
    return new Response(r.body, { status: r.status, headers: { "content-type": r.headers.get("content-type") ?? "application/json", ...(r.headers.get("retry-after") ? { "retry-after": r.headers.get("retry-after")! } : {}) } });
  }
  const tokens = (await r.json()) as TokenResponse;
  const res = NextResponse.json({ ok: true });
  setTokenCookies(res, tokens);
  return res;
}
