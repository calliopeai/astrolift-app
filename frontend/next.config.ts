import type { NextConfig } from "next";
import { withSentryConfig } from "@sentry/nextjs";
import createNextIntlPlugin from "next-intl/plugin";

const withNextIntl = createNextIntlPlugin("./i18n/request.ts");

const apiOrigin = (process.env.NEXT_PUBLIC_API_ORIGIN ?? "http://localhost:8000").replace(/\/$/, "");

// In dev we want the browser to talk to the API on the same origin so
// the Django sessionid cookie (HttpOnly + SameSite=Lax) lands on the
// jar Next.js reads. These rewrites proxy every backend route the UI
// cares about — admin, GraphQL, REST, OAuth — so dev matches the prod
// shape (single LB, single origin) exactly.
const nextConfig: NextConfig = {
  // Standalone output for slim production container images. Next.js
  // copies only the runtime files it needs into .next/standalone, so
  // the runner stage of Dockerfile skips the full node_modules tree.
  output: "standalone",
  // Django requires trailing slashes (APPEND_SLASH redirects GETs;
  // POSTs with a body hard-error). Skip Next.js's automatic 308
  // /foo/ → /foo redirect so the GraphQL endpoint at
  // /app/gql/config/ proxies cleanly to the backend.
  skipTrailingSlashRedirect: true,
  async rewrites() {
    // Backend proxy rewrites so the browser talks to the API on this same
    // origin (the Django sessionid cookie lands on the jar Next.js reads).
    return [
      // Explicit rule for the GraphQL endpoint preserves the trailing
      // slash that Django requires (POST + APPEND_SLASH won't redirect).
      { source: "/app/gql/config/", destination: `${apiOrigin}/app/gql/config/` },
      { source: "/app/:path*/", destination: `${apiOrigin}/app/:path*/` },
      { source: "/app/:path*", destination: `${apiOrigin}/app/:path*` },
      { source: "/health/:path*", destination: `${apiOrigin}/health/:path*` },
    ];
  },
};

export default withSentryConfig(withNextIntl(nextConfig), {
  org: process.env.SENTRY_ORG,
  project: process.env.SENTRY_PROJECT,
  silent: !process.env.CI,
  widenClientFileUpload: true,
  sourcemaps: { disable: true },
  disableLogger: true,
  automaticVercelMonitors: true,
});
