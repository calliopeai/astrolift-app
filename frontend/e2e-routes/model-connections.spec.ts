import { expect, test } from "@playwright/test";
import en from "../messages/en.json";

const copy = en.models.shared.connections;
const requestId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";

test("rendered request queue reaches current detail, reviewer approval and separate pending connection", async ({
  page,
  context,
}, testInfo) => {
  const api = `http://127.0.0.1:${process.env.ROUTE_API_PORT ?? 6172}`;
  expect((await context.request.post(`${api}/observations/reset`)).status()).toBe(204);
  await context.addCookies([
    { name: "sessionid", value: "owner", url: testInfo.project.use.baseURL! },
    { name: "backend_jwt", value: "route-fixture-token", url: testInfo.project.use.baseURL! },
  ]);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/models");
  await page.getByRole("link", { name: copy.viewRequests, exact: true }).click();
  await expect(page.getByRole("heading", { name: copy.myRequests, exact: true })).toBeVisible();
  const requestLink = page.locator(`a[href="/models/connections/${requestId}?version=1"]`).first();
  await expect(requestLink).toBeVisible();
  await requestLink.click();
  await expect(page).toHaveURL(new RegExp(`/models/connections/${requestId}\\?version=1$`));
  await expect(page.getByRole("heading", { name: copy.openRequest, exact: true })).toBeVisible();
  const observedVersion = page
    .getByRole("term")
    .filter({ hasText: copy.version })
    .locator("..")
    .getByRole("definition");
  await expect(observedVersion).toHaveText("2");
  await expect(page.getByRole("button", { name: copy.cancelRequest, exact: true })).toBeEnabled();
  await expect(page.getByRole("button", { name: copy.connect, exact: true })).toHaveCount(0);
  expect(await (await context.request.get(`${api}/observations/model-writes`)).json()).toEqual([]);
  await page.getByRole("link", { name: copy.backToRequests, exact: true }).click();
  await page.getByRole("button", { name: copy.reviewInbox, exact: true }).click();
  await expect(page.getByRole("heading", { name: copy.reviewInbox, exact: true })).toBeVisible();
  await page
    .locator(`a[href="/models/connections/${requestId}?version=1&review=1"]`)
    .first()
    .click();
  await expect(observedVersion).toHaveText("2");
  await page.getByRole("button", { name: copy.approve, exact: true }).click();
  const confirmation = page.getByRole("alertdialog");
  await expect(confirmation).toContainText(copy.decisionNotice);
  expect(await (await context.request.get(`${api}/observations/model-writes`)).json()).toEqual([]);
  await confirmation.getByRole("button", { name: copy.approve, exact: true }).click();
  await expect(page.getByText(copy.decisionSaved, { exact: true })).toBeVisible();
  await expect(observedVersion).toHaveText("3");
  await expect(page.getByText(copy.approvedNotice, { exact: true })).toBeVisible();
  const approval = {
    operationName: "ApproveModelConnectionRequest",
    input: { id: requestId, ifMatchVersion: 2 },
  };
  expect(await (await context.request.get(`${api}/observations/model-writes`)).json()).toEqual([
    approval,
  ]);
  await expect(page.getByRole("button", { name: copy.connect, exact: true })).toHaveCount(0);
  await page.getByRole("link", { name: copy.backToRequests, exact: true }).click();
  await page.locator(`a[href="/models/connections/${requestId}?version=3"]`).first().click();
  await page.getByRole("button", { name: copy.connect, exact: true }).click();
  await expect(confirmation).toContainText(copy.finalizeNotice);
  expect(await (await context.request.get(`${api}/observations/model-writes`)).json()).toEqual([
    approval,
  ]);
  await confirmation.getByRole("button", { name: copy.connect, exact: true }).click();
  await expect(page.getByText(copy.connectionRecorded, { exact: true }).first()).toBeVisible();
  expect(await (await context.request.get(`${api}/observations/model-writes`)).json()).toEqual([
    approval,
    {
      operationName: "FinalizeModelConnectionRequest",
      input: { id: requestId, ifMatchVersion: 3 },
    },
  ]);
  await page.getByRole("link", { name: copy.backToRequests, exact: true }).click();
  await page.getByRole("link", { name: copy.model, exact: true }).first().click();
  await page.getByRole("link", { name: /Controlled shared CPU model/ }).click();
  await expect(page.getByRole("row").filter({ hasText: "MODEL_REVIEWED_" })).toContainText(
    "Pending restart and readiness"
  );
  await expect(page.getByRole("button", { name: "Run model test", exact: true })).toBeDisabled();
  expect(errors).toEqual([]);
  expect(await (await context.request.get(`${api}/observations`)).json()).toEqual({
    errors: [],
    mutations: 0,
    promptInvocations: [],
  });
});
