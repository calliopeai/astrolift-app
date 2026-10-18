// #1815 steps 2 and 4: the first fleet agent's Overview shows its runtime,
// recent activity and run sections, and the overseer input is not offered
// unless a run is RUNNING and the session holds agent_task.send_input. UI
// state only: nothing is ever sent.
import type { Page } from "@playwright/test";

import { expect, messages, test, type GraphqlCall } from "./live";

const overview = messages.agentOverview;

async function openFirstAgent(page: Page): Promise<string | null> {
  await page.goto("/fleet");
  await expect(
    page.getByRole("heading", { name: messages.fleetOverview.roster, exact: true })
  ).toBeVisible();
  const links = page.locator('main a[href^="/agents/"]:not([href^="/agents/runs/"])');
  await links
    .first()
    .waitFor({ timeout: 15_000 })
    .catch(() => undefined);
  const href = (await links.count()) ? await links.first().getAttribute("href") : null;
  if (href) await page.goto(href);
  return href;
}

function taskStatuses(call: GraphqlCall | null): string[] {
  const page = call?.data?.agentTasksPage as { items?: { status: string }[] } | undefined;
  return (page?.items ?? []).map((task) => task.status.toLowerCase());
}

function sendInputGranted(calls: GraphqlCall[]): boolean | null {
  const call = calls.findLast((c) => Array.isArray(c.data?.astroliftMyPermissions));
  return call
    ? (call.data!.astroliftMyPermissions as string[]).includes("agent_task.send_input")
    : null;
}

test("agent overview shows runtime, activity and runs", async ({ page, live }) => {
  const href = await openFirstAgent(page);
  live.report.agent = href;
  test.skip(!href, "the fleet lists no agents");
  for (const section of [overview.latestRun, overview.runtime, overview.recentRuns]) {
    await expect(page.getByRole("heading", { name: section, exact: true })).toBeVisible();
  }
  await live.screenshot("agent-overview");
  expect(live.graphql.filter((call) => call.status >= 400)).toEqual([]);
});

test("overseer input is gated on a running run and the permission", async ({ page, live }) => {
  const href = await openFirstAgent(page);
  test.skip(!href, "the fleet lists no agents");
  await expect(page.getByRole("heading", { name: overview.latestRun, exact: true })).toBeVisible();
  const statuses = taskStatuses(await live.latest("ListAgentTasksPage"));
  const running = statuses.includes("running");
  const granted = sendInputGranted(live.graphql);
  const input = page.getByRole("textbox", { name: messages.agentFrame.input.ariaLabel });
  const shown = await input.isVisible();
  live.report.overseer = {
    agent: href,
    taskStatuses: statuses,
    running,
    sendInput: granted,
    shown,
  };
  await live.screenshot("overseer");
  if (!running) expect(shown, "overseer input offered with no RUNNING run").toBe(false);
  if (granted === false)
    expect(shown, "overseer input offered without agent_task.send_input").toBe(false);
});
