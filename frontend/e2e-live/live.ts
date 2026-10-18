// The read-only signed-in smoke (#1815, #2271). Every spec takes the `live`
// fixture: it aborts every GraphQL mutation and every non-GET call before it
// leaves the browser and fails the test if one was attempted, records each
// GraphQL response for the checks, and adds the test's findings to the
// redacted report (e2e-live/report.ts).
import { readdirSync, readFileSync } from "node:fs";
import { expect, test as base, type Page, type Route, type TestInfo } from "@playwright/test";
import { parse, type OperationDefinitionNode } from "graphql";

export { expect };

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);
// Same-origin session plumbing: these move the browser's own token between
// the page and its cookie and write nothing on the server.
const SESSION_PATHS = new Set(["/api/session/getToken", "/api/auth/store-token"]);
const TELEMETRY_HOST = /(^|\.)sentry\.io$|(^|\.)ingest\./;

export const messages = JSON.parse(readFileSync("messages/en.json", "utf8"));

/**
 * Names the route-walk fixtures render (e2e-routes): every "Controlled ..."
 * literal in the fixture modules plus the generic generator's defaults. None
 * of them may appear on a live page.
 */
export const FIXTURE_NAMES = [
  ...new Set(
    readdirSync("e2e-routes")
      .filter((file) => file.endsWith(".mjs"))
      .flatMap((file) => [
        ...readFileSync(`e2e-routes/${file}`, "utf8").matchAll(/"(Controlled [^"]+)"/g),
      ])
      .map(([, name]) => name)
  ),
  "Fixture",
  "route-group",
  "route-test@example.test",
  "example.test",
];

export function fixtureNamesIn(text: string): string[] {
  return FIXTURE_NAMES.filter((name) =>
    new RegExp(`\\b${name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`).test(text)
  );
}

export interface GraphqlCall {
  operationName: string;
  status: number;
  data: Record<string, unknown> | null;
  errors: number;
}

export interface Live {
  graphql: GraphqlCall[];
  report: Record<string, unknown>;
  screenshot(name: string): Promise<void>;
  /** The latest response of an operation, waiting for it if none arrived yet. */
  latest(operationName: string, timeout?: number): Promise<GraphqlCall | null>;
}

function operationTypes(body: string | null): { types: string[]; names: string[] } {
  try {
    const payloads = [JSON.parse(body ?? "")].flat();
    const out = { types: [] as string[], names: [] as string[] };
    for (const payload of payloads) {
      const ops = parse(payload.query).definitions.filter(
        (d): d is OperationDefinitionNode => d.kind === "OperationDefinition"
      );
      const op = ops.find((d) => d.name?.value === payload.operationName) ?? ops[0];
      out.types.push(op?.operation ?? "unknown");
      out.names.push(op?.name?.value ?? "anonymous");
    }
    return out;
  } catch {
    // A body this guard cannot read is treated as a write.
    return { types: ["unknown"], names: ["unreadable"] };
  }
}

// On the context, so a popup or a second tab is guarded too.
async function guard(page: Page, blocked: string[], telemetry: string[], origin: string) {
  const context = page.context();
  await context.route("**/*", async (route: Route) => {
    const request = route.request();
    const method = request.method();
    const url = new URL(request.url());
    if (SAFE_METHODS.has(method)) return route.continue();
    if (url.pathname.includes("/gql/")) {
      const { types, names } = operationTypes(request.postData());
      if (types.every((type) => type === "query")) return route.continue();
      blocked.push(`${method} ${url.pathname} ${types.join(",")} ${names.join(",")}`);
      return route.abort("blockedbyclient");
    }
    if (url.origin === origin && SESSION_PATHS.has(url.pathname)) return route.continue();
    if (TELEMETRY_HOST.test(url.hostname)) {
      telemetry.push(`${method} ${url.hostname}`);
      return route.abort("blockedbyclient");
    }
    blocked.push(`${method} ${url.origin}${url.pathname}`);
    return route.abort("blockedbyclient");
  });
  // Subscriptions ride the socket; a mutation sent over it is dropped. Any
  // other socket (the exec terminal is a shell) is refused.
  await context.routeWebSocket(/.*/, (socket) => {
    if (!new URL(socket.url()).pathname.includes("/gql/")) {
      blocked.push(`WS ${new URL(socket.url()).pathname}`);
      return socket.close();
    }
    const server = socket.connectToServer();
    socket.onMessage((message) => {
      try {
        const frame = JSON.parse(String(message));
        if (frame.type === "subscribe") {
          const { types, names } = operationTypes(JSON.stringify(frame.payload));
          if (!types.every((type) => type === "subscription" || type === "query")) {
            blocked.push(`WS ${types.join(",")} ${names.join(",")}`);
            return;
          }
        }
      } catch {
        // Non-JSON frames are not GraphQL operations.
      }
      server.send(message);
    });
  });
}

function record(page: Page, graphql: GraphqlCall[]) {
  page.on("response", async (response) => {
    const request = response.request();
    if (!new URL(request.url()).pathname.includes("/gql/") || request.method() !== "POST") return;
    const { names } = operationTypes(request.postData());
    let body: { data?: Record<string, unknown>; errors?: unknown[] } = {};
    try {
      body = await response.json();
    } catch {
      // A non-JSON body (an HTML error page) keeps only its status.
    }
    graphql.push({
      operationName: names.join(","),
      status: response.status(),
      data: body.data ?? null,
      errors: body.errors?.length ?? 0,
    });
  });
}

function slug(testInfo: TestInfo) {
  return testInfo.title.replace(/[^a-z0-9]+/gi, "-").toLowerCase();
}

export const test = base.extend<{ live: Live }>({
  // auto: the guard is on for every test, even one that never reads `live`.
  live: [
    async ({ page, baseURL }, provide, testInfo) => {
      const blocked: string[] = [];
      const telemetry: string[] = [];
      const graphql: GraphqlCall[] = [];
      const report: Record<string, unknown> = {};
      const screenshots: string[] = [];
      await guard(page, blocked, telemetry, new URL(baseURL!).origin);
      record(page, graphql);
      await provide({
        graphql,
        report,
        async screenshot(name) {
          const path = testInfo.outputPath(`${slug(testInfo)}-${name}.png`);
          await page.screenshot({ path, fullPage: true });
          screenshots.push(path);
        },
        async latest(operationName, timeout = 30_000) {
          await expect
            .poll(() => graphql.some((call) => call.operationName === operationName), { timeout })
            .toBe(true)
            .catch(() => undefined);
          return graphql.findLast((call) => call.operationName === operationName) ?? null;
        },
      });
      testInfo.annotations.push({
        type: "live",
        description: JSON.stringify({
          ...report,
          graphql: graphql.map(({ operationName, status, errors }) => ({
            operationName,
            status,
            errors,
          })),
          blockedWrites: blocked,
          blockedTelemetry: telemetry,
          screenshots,
        }),
      });
      expect(blocked, "the smoke attempted a write; it was aborted").toEqual([]);
    },
    { auto: true },
  ],
});
