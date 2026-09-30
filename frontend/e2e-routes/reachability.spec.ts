import { readFileSync, writeFileSync } from "node:fs";
import { test, expect } from "@playwright/test";

const inventory = [
  ...readFileSync("ROUTES.md", "utf8").matchAll(/^\| `([^`]+)` \| ([^|]+) \|/gm),
].map(([, path, kind]) => ({ path, kind: kind.trim() }));
const match = (pattern: string, path: string) =>
  new RegExp(`^${pattern.replace(/\[[^\]]+\]/g, "[^/]+")}/?$`).test(path);

test("owner can reach every active page from rendered navigation", async ({
  page,
  context,
}, testInfo) => {
  // The complete graph visits hundreds of URLs; each navigation stays bounded.
  test.setTimeout(900_000);
  const reset = await context.request.post(
    `http://127.0.0.1:${process.env.ROUTE_API_PORT ?? 6172}/observations/reset`
  );
  expect(reset.status()).toBe(204);
  await context.addCookies([
    { name: "sessionid", value: "owner", url: testInfo.project.use.baseURL! },
    { name: "backend_jwt", value: "route-fixture-token", url: testInfo.project.use.baseURL! },
  ]);
  // No fixture events arrive during this read-only walk. Complete the real
  // subscription handshake without fabricating mutations or live updates.
  await page.routeWebSocket("**/app/gql/config/ws/", (socket) => {
    socket.onMessage((message) => {
      const frame = JSON.parse(String(message));
      if (frame.type === "connection_init") socket.send(JSON.stringify({ type: "connection_ack" }));
      if (frame.type === "ping") socket.send(JSON.stringify({ type: "pong" }));
    });
  });
  const runtimeErrors: string[] = [];
  page.on("pageerror", (error) => runtimeErrors.push(`${page.url()}: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error") runtimeErrors.push(`${page.url()}: ${message.text()}`);
  });
  page.setDefaultTimeout(15_000);
  page.setDefaultNavigationTimeout(20_000);
  const visited = new Map<string, string>();
  const queued = new Set<string>(["/dashboard"]);
  const edges: Record<string, string[]> = {};
  const errors: string[] = [];
  for (const path of queued) {
    if (visited.has(path)) continue;
    writeFileSync(
      testInfo.outputPath("route-graph.json"),
      JSON.stringify({ visited: [...visited], edges, queued: [...queued] }, null, 2)
    );
    console.log("visit", path);
    const declared = inventory.find((route) =>
      match(route.path, new URL(path, testInfo.project.use.baseURL!).pathname)
    );
    if (declared?.kind.includes("https://")) {
      const alias = await context.request.get(path, { maxRedirects: 0 });
      const target = declared.kind.split("`")[1];
      expect(alias.status(), path).toBeLessThan(400);
      if (alias.status() >= 300) expect(alias.headers().location).toBe(target);
      else {
        const html = await alias.text();
        expect(html.includes(`url=${target}"`) && html.includes('id="__next-page-redirect"')).toBe(
          true
        );
      }
      visited.set(path, path);
      continue;
    }
    const response = await page.goto(path, { waitUntil: "networkidle" });
    expect(response?.status(), path).toBeLessThan(400);
    const actual = new URL(page.url()).pathname;
    visited.set(path, actual);
    await expect(
      page
        .getByRole("navigation", { name: "Main", exact: true })
        .getByRole("link", { name: "Apps", exact: true })
    ).toBeVisible();
    // The account menu contains legitimate navigation that is not mounted
    // until opened. Use its real disclosure rather than a test-only href list.
    const hrefs = new Set<string>();
    const collect = async () => {
      for (const href of await page
        .locator("a[href]")
        .evaluateAll((links) => links.map((link) => link.getAttribute("href")!)))
        hrefs.add(href);
    };
    await collect();
    // The event list's default aggregation hides individual event links.
    // Switch through the real read-only control to exercise its raw list.
    if (actual === "/events") {
      const aggregate = page.getByRole("checkbox", { name: "Group repeats" });
      await aggregate.uncheck();
      await page.waitForLoadState("networkidle");
      await collect();
    }
    // Breadcrumb switchers and row overflow menus also expose navigation.
    // Opening disclosures is read-only; never click their action items.
    const disclosures = page.locator(
      'nav button[aria-haspopup="menu"], header button[aria-haspopup="menu"], table button[aria-haspopup="menu"], button[aria-label="Run lists"][aria-haspopup="menu"]'
    );
    for (let index = 0; index < (await disclosures.count()); index++) {
      const disclosure = disclosures.nth(index);
      if (!(await disclosure.isVisible()) || !(await disclosure.isEnabled())) continue;
      await disclosure.press("Enter", { timeout: 30_000 });
      await expect(page.getByRole("menu").first()).toBeVisible();
      await collect();
      // Escape closes a submenu before its parent disclosure.
      for (let depth = 0; depth < 3 && (await page.getByRole("menu").count()); depth++)
        await page.keyboard.press("Escape");
      await expect(page.getByRole("menu")).toHaveCount(0);
    }
    edges[path] = [...hrefs];
    for (const href of hrefs) {
      if (!href.startsWith("/") || href.startsWith("//")) continue;
      const url = new URL(href, page.url());
      if (url.searchParams.has("onboarding")) continue;
      const child = url.pathname + url.search;
      if (
        !url.searchParams.has("tab") &&
        !url.searchParams.has("section") &&
        url.searchParams.get("view") !== "groups" &&
        [...queued].some((existing) => {
          const prior = new URL(existing, testInfo.project.use.baseURL!);
          return (
            prior.pathname === url.pathname &&
            (!prior.search || prior.search === url.search) &&
            Boolean(url.search)
          );
        })
      )
        continue;
      if (inventory.some((route) => route.kind !== "flagged" && match(route.path, url.pathname)))
        queued.add(child);
    }
    if (await page.getByText(/Application error:|This page could not be found/).count())
      errors.push(path);
  }
  writeFileSync(
    testInfo.outputPath("route-graph.json"),
    JSON.stringify({ visited: [...visited], edges }, null, 2)
  );
  await testInfo.attach("rendered-route-graph", {
    body: JSON.stringify({ visited: [...visited], edges }, null, 2),
    contentType: "application/json",
  });
  const missing = inventory.filter(
    (route) =>
      route.kind === "page" && ![...visited.values()].some((path) => match(route.path, path))
  );
  expect(errors, "Pages must render without a Next error boundary").toEqual([]);
  expect(runtimeErrors, "Pages must render without client runtime errors").toEqual([]);
  const observations = await context.request.get(
    `http://127.0.0.1:${process.env.ROUTE_API_PORT ?? 6172}/observations`
  );
  expect(
    await observations.json(),
    "Every fixture query must match SDL and the walk must be read-only"
  ).toEqual({ errors: [], mutations: 0, promptInvocations: [] });
  expect(
    missing,
    "Active pages must have a path from actual nav, account menu, and in-page links"
  ).toEqual([]);
});

const fixturePath = (pattern: string) =>
  pattern.replace(/\[([^\]]+)\]/g, (_, parameter: string) =>
    /id$/i.test(parameter) ? "11111111-1111-4111-8111-111111111111" : "fixture"
  );

async function authenticate(
  context: import("@playwright/test").BrowserContext,
  role: string,
  baseURL: string
) {
  await context.addCookies([
    { name: "sessionid", value: role, url: baseURL },
    { name: "backend_jwt", value: "route-fixture-token", url: baseURL },
  ]);
}

test("every compatibility alias resolves to an active route and parked routes remain 404", async ({
  context,
  page,
}, testInfo) => {
  await authenticate(context, "owner", testInfo.project.use.baseURL!);
  for (const route of inventory) {
    if (route.kind === "page") continue;
    const path = fixturePath(route.path);
    const response = await context.request.get(path, { maxRedirects: 0 });
    if (route.kind === "flagged") {
      // A client notFound() can stream a 200 shell before the 404 boundary.
      // Check the actual production boundary as well as non-streamed status.
      if (response.status() !== 404) {
        await page.goto(path, { waitUntil: "networkidle" });
        await expect(page.getByText("Page not found", { exact: true })).toBeVisible();
        await expect(page.getByRole("link", { name: "Go home", exact: true })).toBeVisible();
      }
      continue;
    }
    expect(response.status(), path).toBeLessThan(400);
    const html = await response.text();
    const meta = html.match(/<meta\b[^>]*>/g)?.find((tag) => tag.includes("__next-page-redirect"));
    let location = response.headers().location ?? meta?.match(/content="\d+;url=([^"]+)"/)?.[1];
    if (!location) {
      // A nested layout can serialize the redirect in RSC rather than a
      // response header. Verify the actual browser redirect in that case.
      await page.goto(path, { waitUntil: "networkidle" });
      expect(new URL(page.url()).pathname + new URL(page.url()).search, path).not.toBe(path);
      location = page.url();
    }
    expect(location, `${path} must be a real redirect, not a silent empty page`).toBeTruthy();
    const target = new URL(location!.replaceAll("&amp;", "&"), testInfo.project.use.baseURL!);
    if (target.origin !== new URL(testInfo.project.use.baseURL!).origin) {
      expect(route.kind, path).toContain(target.origin);
      continue;
    }
    expect(
      inventory.some(
        (candidate) => candidate.kind !== "flagged" && match(candidate.path, target.pathname)
      ),
      `${path} redirects to undeclared ${target.pathname}`
    ).toBe(true);
  }
});

test("reader navigation opens read surfaces and hides pipeline creation", async ({
  page,
  context,
}, testInfo) => {
  await authenticate(context, "reader", testInfo.project.use.baseURL!);
  await page.goto("/dashboard");
  const nav = page.getByRole("navigation", { name: "Main", exact: true });
  await expect(nav.getByRole("link", { name: "Pipelines", exact: true })).toBeVisible();
  await nav.getByRole("link", { name: "Pipelines", exact: true }).click();
  await expect(page).toHaveURL(/\/pipelines$/);
  await expect(page.getByRole("heading", { name: "Pipelines", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "New pipeline" })).toHaveCount(0);
  const pipeline = page.locator('a[href^="/pipelines/"]').first();
  await pipeline.click();
  await expect(page.getByRole("link", { name: "Secrets", exact: true })).toBeVisible();
  await page.getByRole("link", { name: "Secrets", exact: true }).click();
  await expect(page).toHaveURL(/\/pipelines\/[^/]+\/secrets$/);
  await expect(page.getByText("Secret values are write-only.", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Add secret" })).toHaveCount(0);
  await expect(page.getByText(/This page could not be found|Application error:/)).toHaveCount(0);
});

test("member navigation keeps Home and reachable self-service settings", async ({
  page,
  context,
}, testInfo) => {
  await authenticate(context, "member", testInfo.project.use.baseURL!);
  await page.goto("/dashboard");
  const nav = page.getByRole("navigation", { name: "Main", exact: true });
  await expect(nav.getByRole("link", { name: "Home", exact: true })).toBeVisible();
  for (const area of ["Agents", "Apps", "Admin"])
    await expect(nav.getByRole("button", { name: area, exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: /account menu/ }).click();
  await page.getByRole("menuitem", { name: "Profile", exact: true }).click();
  await expect(page).toHaveURL(/\/settings\/profile$/);
  await expect(page.getByRole("heading", { name: "Profile", exact: true })).toBeVisible();
  await expect(page.getByText(/This page could not be found|Application error:/)).toHaveCount(0);
});

test("app Pods panel uses server-safe preloads in the production route", async ({
  page,
  context,
}, testInfo) => {
  await authenticate(context, "owner", testInfo.project.use.baseURL!);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const response = await page.goto("/apps/fixture/logs?section=metrics&panel=pods", {
    waitUntil: "networkidle",
  });
  expect(response?.status()).toBeLessThan(400);
  await expect(page.getByRole("table", { name: "Pods", exact: true })).toBeVisible();
  expect(errors).toEqual([]);
});

test("cold workflow run link waits for its workflow context", async ({
  page,
  context,
}, testInfo) => {
  await authenticate(context, "owner", testInfo.project.use.baseURL!);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const response = await page.goto("/workflows/fixture/runs/11111111-1111-4111-8111-111111111111", {
    waitUntil: "networkidle",
  });
  expect(response?.status()).toBeLessThan(400);
  await expect(page.getByText(/Application error:|This page could not be found/)).toHaveCount(0);
  await expect(page.getByRole("navigation", { name: "Workflow sections" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "run 11111111", exact: true })).toBeVisible();
  expect(errors).toEqual([]);
});

test("anonymous protected routes redirect to login", async ({ page }) => {
  await page.goto("/dashboard", { waitUntil: "networkidle" });
  await expect(page).toHaveURL(/\/auth\/login(?:\?|$)/);
  await expect(page.getByRole("navigation", { name: "Main", exact: true })).toHaveCount(0);
});

for (const path of ["/projects/fixture", "/settings/visualizations"]) {
  test(`cold ${path} hydrates without replacing server markup`, async ({
    page,
    context,
  }, testInfo) => {
    await authenticate(context, "owner", testInfo.project.use.baseURL!);
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const response = await page.goto(path, { waitUntil: "networkidle" });
    expect(response?.status()).toBeLessThan(400);
    await expect(page.getByText(/Application error:|Page not found/)).toHaveCount(0);
    if (path === "/projects/fixture")
      await expect(page.getByRole("navigation", { name: "breadcrumb", exact: true })).toBeVisible();
    else await expect(page.getByText("Fleet view", { exact: true })).toBeVisible();
    expect(errors).toEqual([]);
  });
}
