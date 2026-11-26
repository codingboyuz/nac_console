import "server-only";
import { refreshTokens, type TokenResponse } from "./backend";

/*
 * The backend rotates refresh tokens and revokes the whole session family when a
 * refresh token is reused. Parallel requests that all hit an expired access token
 * would each try to refresh with the same token, so refreshes are single-flighted
 * per refresh token and the outcome is remembered briefly for late arrivals.
 */
const inflight = new Map<string, { promise: Promise<TokenResponse | null>; at: number }>();
const KEEP_MS = 30_000;

export function refreshOnce(refreshToken: string): Promise<TokenResponse | null> {
  const now = Date.now();
  for (const [k, v] of inflight) if (now - v.at > KEEP_MS) inflight.delete(k);
  const hit = inflight.get(refreshToken);
  if (hit) return hit.promise;
  const promise = refreshTokens(refreshToken).catch(() => null);
  inflight.set(refreshToken, { promise, at: now });
  return promise;
}
