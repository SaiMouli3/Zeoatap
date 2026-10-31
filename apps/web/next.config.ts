import type { NextConfig } from "next";
import { PHASE_DEVELOPMENT_SERVER } from "next/constants";

// Production: a fully static export (./out) that the Go core serves at "/".
// trailingSlash makes every route a directory with index.html, which a plain
// file server resolves without rewrite rules (/incidents/ -> incidents/index.html).
//
// Development: `next dev` proxies /api/* to the core (or the mock server).
// Rewrites are not supported by static export, so they exist only in dev.
const API_ORIGIN = process.env.ZEOATAP_API_ORIGIN ?? "http://127.0.0.1:8080";

export default function config(phase: string): NextConfig {
  const base: NextConfig = {
    reactStrictMode: true,
    trailingSlash: true,
    poweredByHeader: false,
    images: { unoptimized: true },
  };
  if (phase === PHASE_DEVELOPMENT_SERVER) {
    return {
      ...base,
      async rewrites() {
        return [{ source: "/api/:path*", destination: `${API_ORIGIN}/api/:path*` }];
      },
    };
  }
  return { ...base, output: "export" };
}
