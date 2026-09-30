import { expect, test } from "@playwright/test";

test("real playground route sends an explicit prompt through the controlled GraphQL transport and saves observed local history", async ({
  page,
  context,
}, testInfo) => {
  const api = `http://127.0.0.1:${process.env.ROUTE_API_PORT ?? 6172}`;
  const reset = await context.request.post(`${api}/observations/reset`);
  expect(reset.status()).toBe(204);
  page.setDefaultTimeout(15000);
  page.setDefaultNavigationTimeout(20000);
  await context.addCookies([
    { name: "sessionid", value: "owner", url: testInfo.project.use.baseURL! },
    { name: "backend_jwt", value: "route-fixture-token", url: testInfo.project.use.baseURL! },
  ]);
  const prompts: Array<Record<string, unknown>> = [];
  page.on("request", (request) => {
    if (request.method() === "POST" && request.url().includes("/app/gql/config/")) {
      try {
        const body = request.postDataJSON();
        if (body.operationName === "PlaygroundPrompt") prompts.push(body.variables.input);
      } catch {}
    }
  });
  await page.goto("/playground");
  await expect(page.getByRole("heading", { name: "Model playground", exact: true })).toBeVisible();
  await expect(
    page.getByText(
      "Each submission sends one user prompt to the selected endpoint. Previous displayed turns are not sent."
    )
  ).toBeVisible();
  await page.getByRole("button", { name: /Controlled local vLLM endpoint/ }).click();
  await expect(
    page.getByText("Ready to attempt a prompt; relay support is unverified.")
  ).toBeVisible();
  await page
    .getByLabel("Prompt", { exact: true })
    .fill("Answer 2 plus 2 for the local browser regression.");
  await page.getByRole("button", { name: "Send prompt", exact: true }).click();
  await expect(page.getByText("Endpoint reply", { exact: true })).toBeVisible();
  await expect(page.getByText("4", { exact: true })).toBeVisible();
  await expect(page.getByText("Reported tokens: 10 · latency: 120 ms")).toBeVisible();
  expect(prompts).toEqual([
    {
      managedServiceId: "11111111-1111-4111-8111-111111111111",
      prompt: "Answer 2 plus 2 for the local browser regression.",
    },
  ]);
  await page
    .getByRole("textbox", { name: "Session title", exact: true })
    .fill("Controlled observed reply");
  await page.getByRole("button", { name: "Save locally", exact: true }).click();
  await page.getByRole("button", { name: "Star", exact: true }).click();
  await page.getByRole("link", { name: "History", exact: true }).click();
  await expect(page.getByRole("heading", { name: "History", exact: true })).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Controlled observed reply", exact: true })
  ).toBeVisible();
  await page.getByRole("link", { name: "Controlled observed reply", exact: true }).click();
  await expect(page.getByText("4", { exact: true })).toBeVisible();
  expect(prompts).toHaveLength(1);
  await page.getByRole("link", { name: "Starred", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Starred", exact: true })).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Controlled observed reply", exact: true })
  ).toBeVisible();
  const observations = await context.request.get(`${api}/observations`);
  expect(await observations.json()).toEqual({
    errors: [],
    mutations: 0,
    promptInvocations: [
      {
        id: "11111111-1111-4111-8111-111111111111",
        prompt: "Answer 2 plus 2 for the local browser regression.",
      },
    ],
  });
  for (const path of ["/playground/topology", "/playground/observability"]) {
    const result = await context.request.get(path);
    if (result.status() !== 404) {
      await page.goto(path);
      await expect(page.getByText("Page not found", { exact: true })).toBeVisible();
    }
  }
});
