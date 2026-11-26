import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import { ACCESS_COOKIE, BACKEND_URL, REFRESH_COOKIE, clearTokenCookies } from "@/lib/server/backend";

export async function POST() {
  const jar = await cookies();
  const access = jar.get(ACCESS_COOKIE)?.value;
  const refresh = jar.get(REFRESH_COOKIE)?.value;
  if (access || refresh) {
    try {
      await fetch(`${BACKEND_URL}/api/v1/auth/logout`, {
        method: "POST",
        headers: {
          "content-type": "application/json",
          ...(access ? { authorization: `Bearer ${access}` } : {}),
        },
        body: JSON.stringify({ refresh_token: refresh ?? null }),
        cache: "no-store",
      });
    } catch {
      // Cookies are cleared regardless; the backend revokes on expiry.
    }
  }
  const res = NextResponse.json({ ok: true });
  clearTokenCookies(res);
  return res;
}
