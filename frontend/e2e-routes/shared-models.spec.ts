import { expect, test } from "@playwright/test";

const modelId = "22222222-2222-4222-8222-222222222222";
const clusterId = "33333333-3333-4333-8333-333333333333";
const providerId = "44444444-4444-4444-8444-444444444444";

test("shared catalogue reaches app-free CPU deployment, honest density and an explicit bounded model test", async ({
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
  page.setDefaultTimeout(15_000);
  await page.goto("/models");
  await expect(page.getByRole("heading", { name: "Shared models", exact: true })).toBeVisible();
  await page.getByRole("link", { name: /Controlled shared CPU model/ }).click();
  await expect(page).toHaveURL(new RegExp(`/models/shared/${modelId}$`));
  await expect(
    page.getByRole("heading", { name: "Controlled shared CPU model", exact: true })
  ).toBeVisible();
  await expect(page.getByText("Qwen/Qwen3-0.6B", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Observed model metrics", exact: true })
  ).toBeVisible();
  await expect(page.getByText("Showing 1 of 1 shared models; snapshot limit 20.")).toBeVisible();
  await expect(
    page.getByText(
      "Capacity requires a verified tenant node pool; unknown or unsupported values are not zero."
    )
  ).toBeVisible();
  await expect(
    page.getByText(
      "Recorded reconciliation permits a bounded test; the actual request can still fail."
    )
  ).toBeVisible();
  expect(await (await context.request.get(`${api}/observations`)).json()).toEqual({
    errors: [],
    mutations: 0,
    promptInvocations: [],
  });
  const prompt = "Answer shared 2 plus 2 for the local browser regression.";
  await page.getByLabel("Prompt", { exact: true }).fill(prompt);
  await page.getByRole("button", { name: "Run model test", exact: true }).click();
  await expect(page.getByText("Model reply", { exact: true })).toBeVisible();
  await expect(page.getByText("4", { exact: true })).toBeVisible();
  await expect(page.getByText("Total tokens: 10", { exact: true })).toBeVisible();
  expect(await (await context.request.get(`${api}/observations`)).json()).toEqual({
    errors: [],
    mutations: 0,
    promptInvocations: [
      {
        managedServiceId: modelId,
        expectedClusterId: clusterId,
        expectedProviderId: providerId,
        expectedVersion: 5,
        prompt,
      },
    ],
  });
  await page.getByRole("link", { name: "Open full cluster catalogue", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Shared models", exact: true })).toBeVisible();
  expect(errors).toEqual([]);
});
