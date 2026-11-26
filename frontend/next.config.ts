import type { NextConfig } from "next";

// Extra hostnames/IPs the dev server accepts (e.g. "192.168.15.152" when the console is opened
// from other machines). start.sh / start.bat set it from --host / NAC_PUBLIC_HOST.
const publicHosts = (process.env.NAC_PUBLIC_HOST ?? "")
  .split(",")
  .map((h) => h.trim())
  .filter(Boolean);

const nextConfig: NextConfig = {
  allowedDevOrigins: publicHosts,
  cacheComponents: true,
  partialPrefetching: true,
  experimental: {
    // Pages are client-rendered behind a session check (/auth/me), so implicit instant-navigation
    // validation of every page only produces dev noise; validate segments that opt in explicitly.
    instantInsights: { validationLevel: "manual-warning" },
  },
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
