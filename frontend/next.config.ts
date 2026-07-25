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
    // An agent IS a RegisteredApp, so its platform pages (config, CI/CD,
    // settings, security, secrets, tokens, deployments…) physically live under
    // /apps/<slug>/*. We want operators to STAY in agent context — the URL
    // should read /agents/<slug>/settings, not jump to /apps. These afterFiles
    // rewrites map the 13 app-platform segments under /agents/* onto the
    // existing /apps/* pages (the agent's own BROCS pillar routes —
    // build/control/observe/overview/run/secure — are real files, so they win
    // over these and are never shadowed). Agent slug == app slug for agents, so
    // :slug carries straight through to the correct app.
    const AGENT_APP_SEGMENTS =
      "config|manifest|webhooks|deployments|environments|domains|managed-services|observability|settings|members|security|secrets|tokens";
    return [
      {
        source: `/agents/:slug/:seg(${AGENT_APP_SEGMENTS})`,
        destination: "/apps/:slug/:seg",
      },
      {
        source: `/agents/:slug/:seg(${AGENT_APP_SEGMENTS})/:rest*`,
        destination: "/apps/:slug/:seg/:rest*",
      },
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
