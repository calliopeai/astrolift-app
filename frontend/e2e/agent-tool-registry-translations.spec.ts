import { expect, test } from "@playwright/test";
import fr from "../messages/fr.json";
import ja from "../messages/ja.json";

const story = (name: string) =>
  `/iframe.html?id=screens-agents-tools-toolregistryscreen--${name}&viewMode=story`;
test("French registry labels and exact tool GUID links remain usable", async ({ page }) => {
  await page.goto(story("french-full"));
  for (const key of ["tool", "skill", "adapter", "description", "handler", "registered"] as const) {
    await expect(
      page.getByRole("columnheader", { name: new RegExp(fr.agentToolRegistry[key]) })
    ).toBeVisible();
  }
  await expect(page.getByPlaceholder(fr.agentToolRegistry.search)).toBeVisible();
  await expect(page.getByRole("link", { name: /Lookup customer/ })).toHaveAttribute(
    "href",
    "/agents/tools/tool-1"
  );
  await page.getByRole("button", { name: fr.shared.list.filter, exact: true }).click();
  await expect(
    page.getByRole("option", { name: new RegExp(fr.agentToolRegistry.adapter) })
  ).toBeVisible();
  await page.getByRole("option", { name: new RegExp(fr.agentToolRegistry.adapter) }).click();
  await expect(
    page.getByRole("option", { name: fr.agentToolRegistry.python_fn, exact: true })
  ).toBeVisible();
  await expect(
    page.getByRole("option", { name: fr.agentToolRegistry.http_endpoint, exact: true })
  ).toBeVisible();
  await expect(
    page.getByRole("option", { name: fr.agentToolRegistry.mcp_server, exact: true })
  ).toBeVisible();
});
test("French Mine explains the current registration-history boundary", async ({ page }) => {
  await page.goto(story("french-mine"));
  await expect(page.getByText(fr.agentToolRegistry.mineNote, { exact: true })).toBeVisible();
  await expect(
    page.getByRole("link", { name: fr.agentToolRegistry.mine, exact: true })
  ).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("link", { name: /Lookup customer/ })).toHaveAttribute(
    "href",
    "/agents/tools/tool-1"
  );
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true
  );
});
test("French empty registry links to the actual skills route", async ({ page }) => {
  await page.goto(story("french-empty"));
  await expect(page.getByText(fr.agentToolRegistry.emptyTitle, { exact: true })).toBeVisible();
  await expect(
    page.getByText(fr.agentToolRegistry.emptyDescription, { exact: true })
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: fr.agentToolRegistry.goToSkills, exact: true })
  ).toHaveAttribute("href", "/agents/skills");
});
test("Japanese read failure preserves diagnostics and localized recovery without false emptiness", async ({
  page,
}) => {
  await page.goto(story("japanese-read-failed"));
  await expect(page.getByText("RAW_TOOL_READ_DIAGNOSTIC", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("button", { name: ja.shared.table.retry, exact: true })
  ).toBeVisible();
  await expect(page.getByText(ja.agentToolRegistry.emptyTitle, { exact: true })).toHaveCount(0);
});
test("Japanese future/prototype adapter and malformed timestamp remain literal and neutral", async ({
  page,
}) => {
  await page.goto(story("japanese-unknown-adapter"));
  await expect(page.getByText("__proto__", { exact: true })).toBeVisible();
  await expect(page.getByText(ja.agentToolRegistry.unknownDate, { exact: true })).toHaveAttribute(
    "title",
    "RAW_INVALID_DATE"
  );
  await page
    .getByRole("button", {
      name: new RegExp(
        ja.shared.shellHeader.switch.replace("{label}", ja.agentToolRegistry.agents)
      ),
    })
    .click();
  await expect(
    page.getByRole("menuitem", { name: ja.agentToolRegistry.skills, exact: true })
  ).toHaveAttribute("href", "/agents/skills");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true
  );
});
