import { expect, test } from "@playwright/test";

test("pending environment clear disables its row while other overrides remain available", async ({
  page,
}) => {
  await page.goto(
    "/iframe.html?id=screens-apps-settings-environmentsettings--clearing&viewMode=story"
  );
  await expect(page.getByRole("button", { name: "Clear replicas override" })).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "Clear memory_limit override" })
  ).toBeEnabled();
});

test("pending bundle attachment and key deletion leave the other bundle actionable", async ({
  page,
}) => {
  await page.goto(
    "/iframe.html?id=screens-agents-list-agentsecretbundles--pending-writes&viewMode=story"
  );
  const attach = page.getByRole("button", { name: "Attach", exact: true });
  await expect(attach.nth(0)).toBeDisabled();
  await expect(attach.nth(1)).toBeEnabled();
  await expect(
    page.getByRole("button", { name: "Delete GITHUB_APP_ID", exact: true })
  ).toBeDisabled();
});

test("padded reference drafts identify the pending normalized save", async ({ page }) => {
  await page.goto(
    "/iframe.html?id=screens-agents-list-agentsecrets--saving-reference&viewMode=story"
  );
  const envVar = page.getByPlaceholder("ENV_VAR", { exact: true });
  // The story play fills the actual inputs; wait for the completed draft.
  await expect(envVar).toHaveValue(" API_KEY ");
  await expect(page.getByRole("button", { name: "Add / update ref" })).toBeDisabled();
});

test("pipeline secrets detail names the pipeline and marks its canonical secrets tab", async ({
  page,
}) => {
  await page.goto("/iframe.html?id=screens-pipelines-pipelinedetail--secrets-route&viewMode=story");
  await expect(page.getByRole("heading", { name: "Build and release", exact: true })).toBeVisible();
  const tab = page.getByRole("link", { name: "Secrets", exact: true });
  await expect(tab).toHaveAttribute("aria-current", "page");
  await expect(tab).toHaveAttribute("href", "/pipelines/pipeline-guid/secrets");
  await expect(page.getByText("No runs yet")).toHaveCount(0);
});

test("pipeline definition refusal is explicit and retryable", async ({ page }) => {
  await page.goto(
    "/iframe.html?id=screens-pipelines-pipelinedetail--definition-error&viewMode=story"
  );
  await expect(page.getByRole("alert")).toContainText("Permission denied");
  await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();
  await expect(page.getByText("Pipeline definition unavailable.")).toHaveCount(0);
});

test("agent repository picker links to the canonical Source providers section", async ({
  page,
}) => {
  await page.goto("/iframe.html?id=screens-agents-new-agentrepopickerstep--full&viewMode=story");
  await expect(
    page.getByRole("link", { name: "Missing a host? Connect another source →" })
  ).toHaveAttribute("href", "/providers#source");
});
