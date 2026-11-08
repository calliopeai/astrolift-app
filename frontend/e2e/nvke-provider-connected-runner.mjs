import { chromium, expect } from "@playwright/test";
let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input),
  story = new URL(config.storyOrigin),
  endpoint = new URL(config.endpoint);
if (
  [story, endpoint].some(
    (url) =>
      url.protocol !== "http:" ||
      url.hostname !== "127.0.0.1" ||
      url.username ||
      url.password ||
      url.search ||
      url.hash
  )
)
  throw Error("Qualification requires owned loopback services");
const browser = await chromium.launch();
try {
  const page = await browser.newPage();
  await page.addInitScript((value) => {
    window.__NVKE_LOCAL_PROVIDERS__ = value;
  }, config);
  let admissions = 0,
    requestId,
    privateReads = 0;
  const identityRequests = [];
  await page.route(config.endpoint, async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    const body = route.request().postDataJSON();
    if (body.operationName === "GetNvkeProviderObservation") privateReads++;
    if (body.operationName === "GetNvkeProviderIdentities")
      identityRequests.push({ search: body.variables.search, after: !!body.variables.after });
    if (body.operationName !== "RequestNvkeProviderObservation") return route.continue();
    admissions++;
    requestId = body.variables.input.requestId;
    const result = await (await route.fetch()).json();
    expect(result.data?.requestNvkeProviderObservation?.ok).toBe(true);
    await route.abort("failed");
  });
  await page.goto(
    `${story.origin}/iframe.html?id=screens-nvke-local-provider-qualification--connected&viewMode=story`
  );
  const collect = page.getByRole("button", { name: "Collect providers", exact: true });
  await expect(collect).toBeEnabled({ timeout: 20000 });
  await collect.click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Review change", exact: true })
    .click();
  const review = page.getByRole("alertdialog");
  await expect(review).toContainText(config.connectionId);
  await expect(review).toContainText(config.managementEndpoint);
  await review.getByRole("button", { name: "Confirm", exact: true }).click();
  const recover = page.getByRole("button", { name: "Recover original receipt", exact: true });
  await expect(recover).toBeEnabled({ timeout: 20000 });
  let operation;
  const query = async (text, variables) =>
    page.evaluate(
      async (value) => {
        const response = await fetch(value.endpoint, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${value.token}`,
            "X-Astrolift-Organization": value.organizationId,
          },
          body: JSON.stringify({ query: value.text, variables: value.variables }),
        });
        return response.json();
      },
      { ...config, text, variables }
    );
  await expect
    .poll(
      async () => {
        const result = await query(
          "query($site:GUID!,$request:String!){astroliftNvkeRequest(siteId:$site,requestId:$request){id version status}}",
          { site: config.siteId, request: requestId }
        );
        operation = result.data?.astroliftNvkeRequest;
        return operation?.status;
      },
      { timeout: 20000 }
    )
    .toBe("SUCCEEDED");
  await expect
    .poll(
      async () => {
        const result = await query(
          "query($site:GUID!){astroliftNvkeSite(siteId:$site){connections{id}}}",
          { site: config.siteId }
        );
        return result.data?.astroliftNvkeSite?.connections?.some(
          (row) => row.id === config.connectionId
        );
      },
      { timeout: 20000 }
    )
    .toBe(false);
  await page.addInitScript(
    (value) => {
      window.__NVKE_LOCAL_PROVIDERS__ = value;
    },
    { ...config, token: config.readerToken, admin: false, observationId: operation.id }
  );
  await page.reload();
  await expect(collect).toHaveCount(0);
  await expect(recover).toBeEnabled({ timeout: 20000 });
  await recover.click();
  await expect(recover).toHaveCount(0, { timeout: 20000 });
  expect(privateReads).toBe(0);
  await page.addInitScript(
    (value) => {
      window.__NVKE_LOCAL_PROVIDERS__ = value;
    },
    { ...config, admin: true, observationId: operation.id }
  );
  await page.reload();
  await expect(page.getByText("Check succeeded", { exact: true })).toBeVisible({ timeout: 20000 });
  await expect(page.getByText("9007199254740993", { exact: true })).toHaveCount(2);
  const older = page.getByRole("button", { name: "Older", exact: true });
  await expect(older).toBeEnabled({ timeout: 20000 });
  const next = page.waitForResponse((response) => {
    if (response.url() !== config.endpoint || response.request().method() !== "POST") return false;
    const body = response.request().postDataJSON();
    return body.operationName === "GetNvkeProviderIdentities" && !!body.variables.after;
  });
  await older.click();
  expect(
    (await (await next).json()).data?.astroliftNvkeProviderIdentitiesPage?.data?.items
  ).toHaveLength(6);
  await expect(page.getByText("extra-030", { exact: true })).toBeVisible();
  const searched = page.waitForResponse((response) => {
    if (response.url() !== config.endpoint || response.request().method() !== "POST") return false;
    const body = response.request().postDataJSON();
    return (
      body.operationName === "GetNvkeProviderIdentities" &&
      body.variables.search === "extra-030" &&
      !body.variables.after
    );
  });
  await page.getByPlaceholder("Search all saved identities").fill("extra-030");
  expect(
    (await (await searched).json()).data?.astroliftNvkeProviderIdentitiesPage?.data?.totalCount
  ).toBe(1);
  await expect(page.getByText("extra-030", { exact: true })).toBeVisible();
  const cleared = page
    .waitForResponse(
      (response) => {
        if (response.url() !== config.endpoint || response.request().method() !== "POST")
          return false;
        const body = response.request().postDataJSON();
        return (
          body.operationName === "GetNvkeProviderIdentities" &&
          body.variables.search === "" &&
          !body.variables.after
        );
      },
      { timeout: 10000 }
    )
    .catch(() => {
      throw Error("Clear search transport: " + JSON.stringify(identityRequests));
    });
  await page.getByPlaceholder("Search all saved identities").fill("");
  expect(
    (await (await cleared).json()).data?.astroliftNvkeProviderIdentitiesPage?.data?.totalCount
  ).toBe(31);
  await expect(page.getByText("extra-000", { exact: true })).toBeVisible();
  await expect(older).toBeEnabled();
  const saved = await page.evaluate(() => Object.values(localStorage).join("\n"));
  expect(saved).toContain(requestId);
  for (const value of [
    config.managementEndpoint,
    "PRIVATE",
    "capnico-system",
    "nico-credentials",
    "9007199254740993",
    "extra-030",
  ])
    expect(saved).not.toContain(value);
  expect(admissions).toBe(1);
  expect(privateReads).toBeGreaterThan(0);
  process.stdout.write(
    JSON.stringify({
      requestId,
      operationId: operation.id,
      admissions,
      readOnlyRecovery: true,
      historicalRead: true,
    })
  );
} finally {
  await browser.close();
}
