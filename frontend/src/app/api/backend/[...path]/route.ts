import { cookies } from "next/headers";
import { NextResponse, type NextRequest } from "next/server";
import {
  ACCESS_COOKIE,
  BACKEND_URL,
  REFRESH_COOKIE,
  clearTokenCookies,
  forwardHeaders,
  problem,
  setTokenCookies,
  type TokenResponse,
} from "@/lib/server/backend";
import { refreshOnce } from "@/lib/server/refresh";

const SEGMENT = /^[A-Za-z0-9._:%@-]+$/;
const PASS_RESPONSE_HEADERS = ["content-type", "retry-after", "location", "x-request-id", "content-disposition"];

async function handle(req: NextRequest, ctx: RouteContext<"/api/backend/[...path]">) {
  const { path } = await ctx.params;
  // Only plain path segments: no traversal out of /api/v1.
  if (!path.length || path.some((s) => s === "." || s === ".." || !SEGMENT.test(s))) {
    return problem(400, "Bad Request", "invalid API path");
  }
  const target = `${BACKEND_URL}/api/v1/${path.map(encodeURIComponent).join("/")}${req.nextUrl.search}`;

  const jar = await cookies();
  let access = jar.get(ACCESS_COOKIE)?.value;
  const refresh = jar.get(REFRESH_COOKIE)?.value;
  let rotated: TokenResponse | null = null;

  if (!access && refresh) {
    rotated = await refreshOnce(refresh);
    access = rotated?.access_token;
  }
  if (!access) {
    const res = problem(401, "Unauthorized", "not signed in") as Response;
    const out = new NextResponse(res.body, res);
    clearTokenCookies(out);
    return out;
  }

  const body = req.method === "GET" || req.method === "HEAD" ? undefined : await req.arrayBuffer();
  const send = (token: string) => {
    const headers = forwardHeaders(req);
    headers.set("authorization", `Bearer ${token}`);
    return fetch(target, { method: req.method, headers, body, cache: "no-store", redirect: "manual" });
  };

  let upstream: Response;
  try {
    upstream = await send(access);
    if (upstream.status === 401 && refresh && !rotated) {
      rotated = await refreshOnce(refresh);
      if (rotated) upstream = await send(rotated.access_token);
    }
  } catch {
    return problem(503, "Backend Unavailable", "backend is not reachable");
  }

  const headers = new Headers();
  for (const name of PASS_RESPONSE_HEADERS) {
    const v = upstream.headers.get(name);
    if (v) headers.set(name, v);
  }
  const res = new NextResponse(upstream.status === 204 ? null : upstream.body, {
    status: upstream.status,
    headers,
  });
  if (rotated) setTokenCookies(res, rotated);
  else if (upstream.status === 401) clearTokenCookies(res);
  return res;
}

export { handle as GET, handle as POST, handle as PUT, handle as PATCH, handle as DELETE };
