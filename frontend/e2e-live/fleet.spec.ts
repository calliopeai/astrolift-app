// #1815 steps 1 and 3: the fleet overview loads from GraphQL and shows no
// route-walk fixture rows.
import { expect, fixtureNamesIn, messages, test } from "./live";

test("fleet overview loads with no fixture rows", async ({ page, live }) => {
  await page.goto("/fleet");
  await expect(
    page.getByRole("heading", { name: messages.fleetOverview.title, exact: true })
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: messages.fleetOverview.roster, exact: true })
  ).toBeVisible();
  await page.waitForLoadState("networkidle", { timeout: 15_000 }).catch(() => undefined);
  await live.screenshot("fleet");

  const fixtures = fixtureNamesIn(await page.locator("main").innerText());
  const failed = live.graphql.filter((call) => call.status >= 400 || call.errors > 0);
  live.report.fixtureNames = fixtures;
  live.report.failedGraphql = failed.map(({ operationName, status, errors }) => ({
    operationName,
    status,
    errors,
  }));
  expect(live.graphql.length, "the page made no GraphQL reads").toBeGreaterThan(0);
  expect(failed, "a GraphQL read failed").toEqual([]);
  if (process.env.LIVE_EXPECT_FIXTURES) {
    // Local run against the route mocks: the detector must see their rows.
    expect(fixtures.length, "the fixture detector found nothing on the mocks").toBeGreaterThan(0);
  } else {
    expect(fixtures, "fixture or mock rows rendered").toEqual([]);
  }
});
