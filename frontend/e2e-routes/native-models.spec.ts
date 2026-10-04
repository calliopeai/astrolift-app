import { expect, test, type Page, type BrowserContext } from "@playwright/test";
import en from "../messages/en.json";
import projections from "../components/screens/models/native-model-projection.fixture.json";

const api = `http://127.0.0.1:${process.env.ROUTE_API_PORT ?? 6172}`;
const connectionId = "99999999-9999-4999-8999-999999999999";
const organizationId = "11111111-1111-4111-8111-111111111111";
const clusterId = "33333333-3333-4333-8333-333333333333";
const providerId = "44444444-4444-4444-8444-444444444444";
const common = projections.rows.find((row) => row.case === "foundation")!.serializedQueryData
  .nativeConnection;
const sourceId = common.source!.sourceId;
const sourceArn = common.source!.sourceArn;
const placement = { organizationId, clusterId, expectedProviderId: providerId };
async function register(page: Page, context: BrowserContext, baseURL: string) {
  expect((await context.request.post(`${api}/observations/reset`)).status()).toBe(204);
  await context.addCookies([
    { name: "sessionid", value: "owner", url: baseURL },
    { name: "backend_jwt", value: "route-fixture-token", url: baseURL },
  ]);
  page.setDefaultTimeout(15000);
  await page.goto("/models");
  await page.getByRole("link", { name: en.models.native.add.connectAction, exact: true }).click();
  await expect(page).toHaveURL(/\/models\/connect$/);
  await page
    .getByRole("button", { name: "Controlled shared cluster · shared-fixture", exact: true })
    .click();
  await page.getByRole("button", { name: en.models.native.connect.load, exact: true }).click();
  await page.getByRole("button", { name: "Claude 3 Haiku", exact: true }).click();
  await expect(page.getByText(sourceArn, { exact: true })).toBeVisible();
  await expect(
    page.getByText(en.models.native.connect.accessUnknown, { exact: true })
  ).toBeVisible();
  await page
    .getByLabel(en.models.native.connect.name, { exact: true })
    .fill("Controlled native connection");
  await page.getByRole("button", { name: en.models.native.connect.review, exact: true }).click();
  await expect(
    page.getByText(en.models.native.connect.registerHelp, { exact: true })
  ).toBeVisible();
  expect(await (await context.request.get(`${api}/observations/model-writes`)).json()).toEqual([]);
  await page.getByRole("button", { name: en.models.native.connect.register, exact: true }).click();
  await expect(page.getByText(en.models.native.connect.registered, { exact: true })).toBeVisible();
  await page
    .getByRole("link", { name: en.models.shared.placement.openDeployment, exact: true })
    .click();
  await expect(page).toHaveURL(new RegExp(`/models/shared/${connectionId}$`));
  await expect(
    page.getByRole("heading", { name: "Controlled native connection", exact: true })
  ).toBeVisible();
  await expect(page.getByText(sourceArn, { exact: true })).toBeVisible();
  await expect(
    page.getByText(en.models.native.observations.inferenceUnknown, { exact: true })
  ).toBeVisible();
  await expect(
    page.getByText(en.models.native.observations.trafficUnsupported, { exact: true })
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Run model test", exact: true })).toHaveCount(0);
  await expect(page.getByLabel("CPU request", { exact: true })).toHaveCount(0);
  const writes = await (await context.request.get(`${api}/observations/model-writes`)).json();
  expect(writes).toEqual([
    {
      operationName: "RegisterBedrockModel",
      input: {
        ...placement,
        expectedClusterVersion: 3,
        expectedProviderVersion: 2,
        sourceKind: "FOUNDATION_MODEL",
        sourceIdentifier: sourceId,
        sourceFingerprint: common.reviewedSourceFingerprint,
        name: "Controlled native connection",
        allowSubscriptions: true,
        sharingMode: "SHARED",
        dedicatedAppId: null,
        ifMatchDedicatedAppVersion: null,
      },
    },
  ]);
}
for (const action of ["inspect", "settings", "subscribe", "remove"] as const) {
  test(`native connection ${action} follows current schema and reports only proven local effects`, async ({
    page,
    context,
  }, testInfo) => {
    const runtimeErrors: string[] = [];
    page.on("pageerror", (error) => runtimeErrors.push(error.message));
    await register(page, context, testInfo.project.use.baseURL!);
    if (action === "settings") {
      const settings = page.getByRole("region", {
        name: en.models.shared.inventory.settings,
        exact: true,
      });
      await settings
        .getByLabel(en.models.native.connect.name, { exact: true })
        .fill("Renamed native connection");
      await settings
        .getByRole("button", { name: en.models.native.details.saveSettings, exact: true })
        .click();
      const dialog = page.getByRole("alertdialog");
      await expect(dialog).toContainText(en.models.native.details.settingsHelp);
      await dialog
        .getByRole("button", { name: en.models.native.details.saveSettings, exact: true })
        .click();
      await expect(
        settings.getByText(en.models.native.details.queued, { exact: true })
      ).toBeVisible();
      await expect(page.getByText(sourceArn, { exact: true })).toBeVisible();
      const writes = await (await context.request.get(`${api}/observations/model-writes`)).json();
      expect(writes[1]).toEqual({
        operationName: "UpdateBedrockModel",
        input: {
          organizationId,
          id: connectionId,
          expectedClusterId: clusterId,
          expectedProviderId: providerId,
          ifMatchVersion: 1,
          name: "Renamed native connection",
          allowSubscriptions: true,
          sharingMode: "SHARED",
          dedicatedAppId: null,
          ifMatchDedicatedAppVersion: null,
        },
      });
    }
    if (action === "subscribe") {
      await page
        .getByRole("button", { name: en.models.shared.connections.add, exact: true })
        .click();
      const intake = page.getByRole("region", {
        name: en.models.shared.connections.add,
        exact: true,
      });
      await intake
        .getByRole("button", { name: "controlled-app / production", exact: true })
        .click();
      await intake
        .getByLabel(en.models.shared.connections.alias, { exact: true })
        .fill("assistant");
      await intake
        .getByRole("button", { name: en.models.shared.connections.review, exact: true })
        .click();
      const dialog = page.getByRole("alertdialog");
      await expect(dialog).toContainText(en.models.native.details.restartImpact);
      await dialog
        .getByRole("button", { name: en.models.shared.connections.connect, exact: true })
        .click();
      await expect(
        intake.getByText(en.models.shared.connections.connectionQueued, { exact: true })
      ).toBeVisible();
      const writes = await (await context.request.get(`${api}/observations/model-writes`)).json();
      expect(writes[1]).toMatchObject({
        operationName: "SubscribeClusterModel",
        input: {
          organizationId,
          modelDeploymentId: connectionId,
          expectedClusterId: clusterId,
          expectedProviderId: providerId,
          ifMatchVersion: 1,
          ifMatchEnvironmentVersion: 4,
          alias: "assistant",
        },
      });
      await expect(page.getByRole("button", { name: "Run model test", exact: true })).toHaveCount(
        0
      );
    }
    if (action === "remove") {
      const settings = page.getByRole("region", {
        name: en.models.shared.inventory.settings,
        exact: true,
      });
      await settings
        .getByRole("button", { name: en.models.native.details.remove, exact: true })
        .click();
      const dialog = page.getByRole("alertdialog");
      await expect(dialog).toContainText(en.models.native.details.removeHelp);
      await dialog
        .getByRole("button", { name: en.models.native.details.remove, exact: true })
        .click();
      await expect(page.getByText(en.models.native.details.removed, { exact: true })).toBeVisible();
      await expect(page.getByText(sourceArn, { exact: true })).toHaveCount(0);
      const writes = await (await context.request.get(`${api}/observations/model-writes`)).json();
      expect(writes[1]).toEqual({
        operationName: "UnregisterBedrockModel",
        input: {
          organizationId,
          id: connectionId,
          expectedClusterId: clusterId,
          expectedProviderId: providerId,
          ifMatchVersion: 1,
        },
      });
      await page.goto("/models");
      await expect(page.getByRole("link", { name: /Controlled native connection/ })).toHaveCount(0);
    }
    expect(runtimeErrors).toEqual([]);
    expect(await (await context.request.get(`${api}/observations`)).json()).toEqual({
      errors: [],
      mutations: 0,
      promptInvocations: [],
    });
  });
}

test("direct native wizard refuses a read-only caller using current server support", async ({
  page,
  context,
}, testInfo) => {
  expect((await context.request.post(`${api}/observations/reset`)).status()).toBe(204);
  await context.addCookies([
    { name: "sessionid", value: "reader", url: testInfo.project.use.baseURL! },
    { name: "backend_jwt", value: "route-fixture-token", url: testInfo.project.use.baseURL! },
  ]);
  await page.goto("/models/connect");
  await expect(
    page.getByText("Controlled native registration refusal.", { exact: true })
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: en.models.native.connect.load, exact: true })
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: en.models.native.connect.register, exact: true })
  ).toHaveCount(0);
  expect(await (await context.request.get(`${api}/observations/model-writes`)).json()).toEqual([]);
  expect(await (await context.request.get(`${api}/observations`)).json()).toEqual({
    errors: [],
    mutations: 0,
    promptInvocations: [],
  });
});
