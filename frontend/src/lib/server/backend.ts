import "server-only";
import type { NextResponse } from "next/server";

export const BACKEND_URL = (process.env.BACKEND_URL ?? "http://localhost:8000").replace(/\/$/, "");
export const ACCESS_COOKIE = "nac_at";
export const REFRESH_COOKIE = "nac_rt";
const REFRESH_TTL = 7 * 24 * 3600;
const secure = process.env.COOKIE_SECURE !== "false";

export type TokenResponse = {
  access_token: string;
  refresh_token: string;
  expires_in: number;
  token_type?: string;
};

export function setTokenCookies(res: NextResponse, tokens: TokenResponse) {
  const base = { httpOnly: true, secure, sameSite: "strict" as const, path: "/" };
  res.cookies.set(ACCESS_COOKIE, tokens.access_token, { ...base, maxAge: tokens.expires_in });
  res.cookies.set(REFRESH_COOKIE, tokens.refresh_token, { ...base, maxAge: REFRESH_TTL });
}

export function clearTokenCookies(res: NextResponse) {
  res.cookies.delete(ACCESS_COOKIE);
  res.cookies.delete(REFRESH_COOKIE);
}

export function forwardHeaders(req: Request): Headers {
  const h = new Headers();
  for (const name of ["content-type", "accept", "idempotency-key", "x-request-id", "user-agent"]) {
    const v = req.headers.get(name);
    if (v) h.set(name, v);
  }
  const fwd = req.headers.get("x-forwarded-for");
  if (fwd) h.set("x-forwarded-for", fwd);
  return h;
}

export async function refreshTokens(refreshToken: string): Promise<TokenResponse | null> {
  const r = await fetch(`${BACKEND_URL}/api/v1/auth/refresh`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken }),
    cache: "no-store",
  });
  if (!r.ok) return null;
  return (await r.json()) as TokenResponse;
}

export function problem(status: number, title: string, detail: string) {
  return Response.json(
    { type: "about:blank", title, status, detail, code: title.toLowerCase().replace(/\s+/g, "_"), message: detail },
    { status, headers: { "content-type": "application/problem+json" } },
  );
}
