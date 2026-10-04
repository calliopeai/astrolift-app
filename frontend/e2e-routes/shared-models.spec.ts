import en from "../messages/en.json";
import { expect, test } from "@playwright/test";

const modelId = "22222222-2222-4222-8222-222222222222";
const clusterId = "33333333-3333-4333-8333-333333333333";
const providerId = "44444444-4444-4444-8444-444444444444";

for (const action of ["update", "deprovision", "refuse-deprovision"] as const) {
  test(`shared model management ${action} requires review and reports the actual outcome`, async ({
    page,
    context,
  }, testInfo) => {
    const api = `http://127.0.0.1:${process.env.ROUTE_API_PORT ?? 6172}`;
    const reset = action === "deprovision" ? "?model=unsubscribed" : "";
    expect((await context.request.post(`${api}/observations/reset${reset}`)).status()).toBe(204);
    await context.addCookies([
      { name: "sessionid", value: "owner", url: testInfo.project.use.baseURL! },
      { name: "backend_jwt", value: "route-fixture-token", url: testInfo.project.use.baseURL! },
    ]);
    page.setDefaultTimeout(15_000);
    await page.goto("/models");
    await page.getByRole("link", { name: /Controlled shared CPU model/ }).click();
    const section = page.getByRole("region", {
      name: en.models.shared.inventory.settings,
      exact: true,
    });
    await page
      .getByLabel("Prompt", { exact: true })
      .fill("Inspect admission without running a prompt.");
    await expect(page.getByRole("button", { name: "Run model test", exact: true })).toBeEnabled();
    if (action === "update") {
      await section.getByLabel("CPU request", { exact: true }).fill("3");
      await section.getByRole("button", { name: "Review resource update", exact: true }).click();
    } else {
      await section.getByRole("button", { name: "Review deprovisioning", exact: true }).click();
    }
    const confirmation = page.getByRole("alertdialog");
    await expect(confirmation).toContainText(
      action === "update"
        ? "All consumers may temporarily lose access."
        : "Persisted data is retained; this action does not request data deletion."
    );
    expect(await (await context.request.get(`${api}/observations/model-writes`)).json()).toEqual(
      []
    );
    await confirmation
      .getByRole("button", {
        name: action === "update" ? "Request resource update" : "Request deprovisioning",
        exact: true,
      })
      .click();
    const input = {
      organizationId: "11111111-1111-4111-8111-111111111111",
      id: modelId,
      expectedClusterId: clusterId,
      expectedProviderId: providerId,
      ifMatchVersion: 5,
      ...(action === "update"
        ? {
            name: "Controlled shared CPU model",
            cpuRequest: "3",
            memoryRequest: "8Gi",
            gpuCount: 0,
            cpuKvCacheGiB: 2,
            allowSubscriptions: true,
            dtype: "FLOAT32",
            maxModelLen: 512,
            maxNumSeqs: 1,
            sharingMode: "SHARED",
            dedicatedAppId: null,
            ifMatchDedicatedAppVersion: null,
          }
        : { deleteData: false }),
    };
    if (action === "refuse-deprovision") {
      await expect(confirmation).toContainText(
        "CONFLICT: Controlled refusal: reconcile all subscription revocations before removal."
      );
      await expect(confirmation).toBeVisible();
      await expect(
        section.getByText("Deprovisioning accepted. Removal is not yet confirmed.")
      ).toHaveCount(0);
      await confirmation.getByRole("button", { name: "Cancel", exact: true }).click();
      await expect(page.getByRole("button", { name: "Run model test", exact: true })).toBeEnabled();
    } else {
      await expect(confirmation).not.toBeVisible();
      await expect(
        section.getByText(
          action === "update"
            ? "Resource update accepted. Restart and readiness are not yet confirmed."
            : "Deprovisioning accepted. Removal is not yet confirmed.",
          { exact: true }
        )
      ).toBeVisible();
      await expect(
        section.getByRole("button", { name: "Review deprovisioning", exact: true })
      ).toBeDisabled();
      await expect(
        page.getByRole("button", { name: "Run model test", exact: true })
      ).toBeDisabled();
    }
    expect(await (await context.request.get(`${api}/observations/model-writes`)).json()).toEqual([
      {
        operationName: action === "update" ? "UpdateClusterModel" : "DeprovisionClusterModel",
        input,
      },
    ]);
    expect(await (await context.request.get(`${api}/observations`)).json()).toEqual({
      errors: [],
      mutations: 0,
      promptInvocations: [],
    });
  });
}

for (const action of ["subscribe", "revoke"] as const) {
  test(`shared model ${action} reviews exact app credentials and remains pending after acceptance`, async ({
    page,
    context,
  }, testInfo) => {
    const api = `http://127.0.0.1:${process.env.ROUTE_API_PORT ?? 6172}`;
    expect((await context.request.post(`${api}/observations/reset`)).status()).toBe(204);
    await context.addCookies([
      { name: "sessionid", value: "owner", url: testInfo.project.use.baseURL! },
      { name: "backend_jwt", value: "route-fixture-token", url: testInfo.project.use.baseURL! },
    ]);
    page.setDefaultTimeout(15_000);
    await page.goto("/models");
    await page.getByRole("link", { name: /Controlled shared CPU model/ }).click();
    if (action === "subscribe")
      await page
        .getByRole("button", { name: en.models.shared.connections.add, exact: true })
        .click();
    const section = page.getByRole("region", {
      name:
        action === "subscribe"
          ? en.models.shared.connections.add
          : en.models.shared.inventory.connections,
      exact: true,
    });
    if (action === "subscribe") {
      await section
        .getByRole("button", { name: "controlled-app / production", exact: true })
        .click();
      await section
        .getByLabel(en.models.shared.connections.alias, { exact: true })
        .fill("assistant");
      await section
        .getByRole("button", { name: en.models.shared.connections.review, exact: true })
        .click();
    } else {
      await section
        .getByRole("row")
        .filter({ hasText: "MODEL_CHAT_" })
        .getByRole("button", { name: "Review revocation", exact: true })
        .click();
    }
    const confirmation = page.getByRole("alertdialog");
    await expect(confirmation).toContainText(
      action === "subscribe"
        ? en.models.shared.connections.restartNotice
        : "All consumers may temporarily lose access."
    );
    expect(await (await context.request.get(`${api}/observations/model-writes`)).json()).toEqual(
      []
    );
    await confirmation
      .getByRole("button", {
        name: action === "subscribe" ? en.models.shared.connections.connect : "Request revocation",
        exact: true,
      })
      .click();
    await expect(
      section.getByText(
        action === "subscribe"
          ? en.models.shared.connections.connectionQueued
          : "Request accepted. Waiting for restart and readiness; the requested access change is not yet confirmed.",
        { exact: true }
      )
    ).toBeVisible();
    if (action === "subscribe")
      await page
        .getByRole("button", { name: en.models.shared.connections.connections, exact: true })
        .click();
    const currentConnections = page.getByRole("region", {
      name: en.models.shared.inventory.connections,
      exact: true,
    });
    await expect(
      currentConnections
        .getByRole("row")
        .filter({ hasText: action === "subscribe" ? "MODEL_ASSISTANT_" : "MODEL_CHAT_" })
    ).toContainText(
      action === "subscribe"
        ? "Pending restart and readiness"
        : "Revocation pending restart and readiness"
    );
    await expect(
      currentConnections.getByRole("row").filter({ hasText: "MODEL_SEARCH_" })
    ).toContainText("Active");
    const input =
      action === "subscribe"
        ? {
            organizationId: "11111111-1111-4111-8111-111111111111",
            modelDeploymentId: modelId,
            expectedClusterId: clusterId,
            expectedProviderId: providerId,
            appEnvironmentId: "55555555-5555-4555-8555-555555555555",
            alias: "assistant",
            ifMatchVersion: 5,
            ifMatchEnvironmentVersion: 4,
          }
        : {
            organizationId: "11111111-1111-4111-8111-111111111111",
            id: "88888888-8888-4888-8888-888888888888",
            expectedClusterId: clusterId,
            expectedProviderId: providerId,
            ifMatchVersion: 2,
            ifMatchDeploymentVersion: 5,
          };
    expect(await (await context.request.get(`${api}/observations/model-writes`)).json()).toEqual([
      {
        operationName: action === "subscribe" ? "SubscribeClusterModel" : "RevokeModelSubscription",
        input,
      },
    ]);
    await expect(page.getByRole("button", { name: "Run model test", exact: true })).toBeDisabled();
    expect(await (await context.request.get(`${api}/observations`)).json()).toEqual({
      errors: [],
      mutations: 0,
      promptInvocations: [],
    });
  });
}

for (const computeMode of ["cpu", "gpu"] as const) {
  test(`Hugging Face ${computeMode} deployment creates a cluster operation without app ownership or claiming readiness`, async ({
    page,
    context,
  }, testInfo) => {
    const api = `http://127.0.0.1:${process.env.ROUTE_API_PORT ?? 6172}`;
    const deploymentName = `Controlled newly created ${computeMode.toUpperCase()} model`;
    expect((await context.request.post(`${api}/observations/reset`)).status()).toBe(204);
    await context.addCookies([
      { name: "sessionid", value: "owner", url: testInfo.project.use.baseURL! },
      { name: "backend_jwt", value: "route-fixture-token", url: testInfo.project.use.baseURL! },
    ]);
    page.setDefaultTimeout(15_000);
    await page.goto("/models");
    await page.getByRole("link", { name: "Host a model", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Host a model", exact: true })).toBeVisible();
    const journey = page.getByRole("navigation", { name: "Hosting setup", exact: true });
    await expect(journey).toContainText("Choose model");
    await expect(journey).toContainText("Choose a model from Hugging Face or import your files.");
    await expect(page.getByRole("button", { name: "Hugging Face", exact: true })).toHaveAttribute(
      "aria-pressed",
      "true"
    );
    await expect(
      page.getByRole("button", { name: "Local model files", exact: true })
    ).toBeEnabled();
    await expect(page.getByText("Runtime compatibility unknown", { exact: true })).toHaveCount(0);
    const modelChoice = page.getByRole("button", { name: "Qwen/Qwen3-0.6B", exact: true });
    await expect(modelChoice).toBeVisible();
    if (computeMode === "cpu")
      await page.screenshot({
        path: "/tmp/astrolift-hosting-ux-source.png",
        animations: "disabled",
      });
    await modelChoice.click();
    await page
      .getByRole("button", { name: "Choose cluster and check access", exact: true })
      .click();
    await page
      .getByRole("button", { name: "Controlled shared cluster · shared-fixture", exact: true })
      .click();
    await expect(page.getByLabel("Deployment name", { exact: true })).toHaveValue("Qwen3-0.6B");
    await page.getByLabel("Deployment name", { exact: true }).fill(deploymentName);
    await page.getByLabel(computeMode.toUpperCase(), { exact: true }).check();
    if (computeMode === "gpu")
      await page.getByLabel("Requested GPU devices", { exact: true }).fill("1");
    await page.getByLabel("Enable named app subscriptions", { exact: true }).check();
    await page.getByRole("button", { name: "Next", exact: true }).click();
    await expect(page.getByRole("radio", { name: "CPU", exact: true })).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: "Controlled shared cluster · shared-fixture", exact: true })
    ).toHaveCount(0);
    const review = page.getByRole("button", { name: "Review deployment", exact: true });
    await expect(
      page.getByText("Read access confirmed for this repository and revision.", { exact: true })
    ).toBeVisible();
    await expect(review).toBeDisabled();
    expect(await (await context.request.get(`${api}/observations/model-writes`)).json()).toEqual(
      []
    );
    await page
      .getByRole("checkbox", {
        name: "I reviewed the model license and permitted use for this deployment.",
        exact: true,
      })
      .check();
    await expect(review).toBeEnabled();
    await expect(journey).toContainText("Ready for review");
    if (computeMode === "cpu") {
      await page
        .getByRole("heading", { name: "3. Review hosting checks", exact: true })
        .scrollIntoViewIfNeeded();
      await page.screenshot({
        path: "/tmp/astrolift-hosting-ux-checks.png",
        animations: "disabled",
      });
      await page.setViewportSize({ width: 768, height: 1024 });
      await page.screenshot({
        path: "/tmp/astrolift-hosting-ux-checks-768.png",
        animations: "disabled",
      });
      await page.setViewportSize({ width: 1440, height: 1000 });
    }
    expect(await (await context.request.get(`${api}/observations/model-writes`)).json()).toEqual(
      []
    );
    await review.click();
    const confirmation = page.getByRole("alertdialog");
    await expect(confirmation).toContainText("Hardware capacity and model fit remain unverified.");
    await confirmation.getByRole("button", { name: "Request deployment", exact: true }).click();
    await expect(
      page.getByText(
        "Deployment request accepted. Scheduling and readiness are not yet confirmed.",
        {
          exact: true,
        }
      )
    ).toBeVisible();
    await expect(page.getByRole("link", { name: "Open deployment", exact: true })).toHaveAttribute(
      "href",
      "/models/shared/77777777-7777-4777-8777-777777777777"
    );
    expect(await (await context.request.get(`${api}/observations/model-writes`)).json()).toEqual([
      {
        operationName: "ProvisionClusterModel",
        input: {
          organizationId: "11111111-1111-4111-8111-111111111111",
          clusterId,
          expectedProviderId: providerId,
          name: deploymentName,
          modelRepo: "Qwen/Qwen3-0.6B",
          revisionSha: "a".repeat(40),
          localArtifactId: null,
          expectedArtifactVersion: null,
          connectionId: null,
          expectedConnectionVersion: null,
          computeMode,
          cpuRequest: "2",
          memoryRequest: "8Gi",
          gpuCount: computeMode === "cpu" ? 0 : 1,
          cpuKvCacheGiB: computeMode === "cpu" ? 2 : null,
          allowSubscriptions: true,
          dtype: null,
          maxModelLen: null,
          maxNumSeqs: null,
        },
      },
    ]);
    await expect(review).toBeDisabled();
    expect(await (await context.request.get(`${api}/observations`)).json()).toEqual({
      errors: [],
      mutations: 0,
      promptInvocations: [],
    });
    await page.getByRole("link", { name: "Open deployment", exact: true }).click();
    await expect(page.getByRole("heading", { name: deploymentName, exact: true })).toBeVisible();
    await expect(
      page.getByRole("definition").filter({ hasText: /^No current reconciliation confirmation/ })
    ).toBeVisible();
    await expect(page.getByRole("button", { name: "Run model test", exact: true })).toBeDisabled();
  });
}

test("read-only model catalogue cannot host through a direct wizard route", async ({
  page,
  context,
}, testInfo) => {
  const api = `http://127.0.0.1:${process.env.ROUTE_API_PORT ?? 6172}`;
  expect((await context.request.post(`${api}/observations/reset`)).status()).toBe(204);
  await context.addCookies([
    { name: "sessionid", value: "reader", url: testInfo.project.use.baseURL! },
    { name: "backend_jwt", value: "route-fixture-token", url: testInfo.project.use.baseURL! },
  ]);
  await page.goto("/models/deploy");
  await expect(page.getByRole("heading", { name: "Host a model", exact: true })).toBeVisible();
  await expect(
    page.getByText("Controlled hosting authority refusal.", { exact: true })
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Host model", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Hugging Face", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Local model files", exact: true })).toBeDisabled();
  expect(await (await context.request.get(`${api}/observations/model-writes`)).json()).toEqual([]);
  expect(await (await context.request.get(`${api}/observations`)).json()).toEqual({
    errors: [],
    mutations: 0,
    promptInvocations: [],
  });
});

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

test("small model wizard preserves steps and submits only its reviewed immutable CPU request", async ({
  page,
  context,
}, testInfo) => {
  const api = `http://127.0.0.1:${process.env.ROUTE_API_PORT ?? 6172}`;
  expect((await context.request.post(`${api}/observations/reset`)).status()).toBe(204);
  await context.addCookies([
    { name: "sessionid", value: "owner", url: testInfo.project.use.baseURL! },
    { name: "backend_jwt", value: "route-fixture-token", url: testInfo.project.use.baseURL! },
  ]);
  await page.goto("/models/deploy");
  await page.getByRole("button", { name: "Choose a small test model", exact: true }).click();
  await page.getByRole("button", { name: "Choose cluster and check access", exact: true }).click();
  const name = page.getByLabel("Deployment name", { exact: true });
  await expect(name).toHaveValue("Qwen2.5-0.5B-Instruct");
  await expect(page.getByRole("button", { name: "Review deployment", exact: true })).toHaveCount(0);
  await page
    .getByRole("button", { name: "Controlled shared cluster · shared-fixture", exact: true })
    .click();
  await page.getByRole("radio", { name: "CPU", exact: true }).check();
  await expect(page.getByLabel("CPU KV-cache request (GiB)", { exact: true })).toHaveValue("1");
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await expect(page.getByRole("radio", { name: "CPU", exact: true })).toHaveCount(0);
  const license = page.getByRole("checkbox", {
    name: "I reviewed the model license and permitted use for this deployment.",
    exact: true,
  });
  await license.check();
  await page
    .getByRole("link", { name: "Review CPU, memory and GPU requests", exact: true })
    .click();
  await expect(page.getByLabel("CPU request", { exact: true })).toBeFocused();
  await expect(name).toHaveValue("Qwen2.5-0.5B-Instruct");
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await expect(license).toBeChecked();
  await expect(
    page.getByRole("link", { name: "Review cluster runtime configuration", exact: true })
  ).toHaveAttribute("target", "_blank");
  expect(await (await context.request.get(`${api}/observations/model-writes`)).json()).toEqual([]);
  await page.getByRole("button", { name: "Review deployment", exact: true }).click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "Request deployment", exact: true })
    .click();
  await expect(page.getByRole("link", { name: "Open deployment", exact: true })).toBeVisible();
  const writes = await (await context.request.get(`${api}/observations/model-writes`)).json();
  expect(writes).toHaveLength(1);
  expect(writes[0].input).toMatchObject({
    name: "Qwen2.5-0.5B-Instruct",
    modelRepo: "Qwen/Qwen2.5-0.5B-Instruct",
    cpuRequest: "1",
    memoryRequest: "4Gi",
    revisionSha: "a".repeat(40),
    clusterId,
    expectedProviderId: providerId,
    computeMode: "cpu",
    gpuCount: 0,
    cpuKvCacheGiB: 1,
    dtype: "FLOAT32",
    maxModelLen: 256,
    maxNumSeqs: 1,
  });
});
