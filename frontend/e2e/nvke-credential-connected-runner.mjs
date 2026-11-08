import { chromium, expect } from "@playwright/test";
let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input),
  story = new URL(config.storyOrigin),
  endpoint = new URL(config.endpoint);
if (
  [story, endpoint].some(
    (url) =>
      url.protocol !== "http:" || url.hostname !== "127.0.0.1" || url.username || url.password
  )
)
  throw Error("Qualification requires owned loopback services");
const browser = await chromium.launch();
try {
  const page = await browser.newPage();
  await page.addInitScript((value) => {
    window.__NVKE_LOCAL_METRICS__ = value;
  }, config);
  let admissions = 0,
    requestId;
  await page.route(config.endpoint, async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    const body = route.request().postDataJSON();
    if (body.operationName !== "RequestNvkeCredentialObservation") return route.continue();
    admissions++;
    requestId = body.variables.input.requestId;
    const response = await route.fetch(),
      result = await response.json();
    expect(result.data?.requestNvkeCredentialObservation?.ok).toBe(true);
    await route.abort("failed");
  });
  await page.goto(
    `${story.origin}/iframe.html?id=screens-nvke-local-credential-qualification--connected&viewMode=story`
  );
  const collect = page.getByRole("button", { name: "Collect credentials", exact: true });
  await expect(collect).toBeEnabled({ timeout: 20000 });
  await collect.click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Review change", exact: true })
    .click();
  const review = page.getByRole("alertdialog");
  await expect(review).toContainText(config.clusterId);
  await expect(review).toContainText(config.metricsEndpoint);
  await review.getByRole("button", { name: "Confirm", exact: true }).click();
  const recover = page.getByRole("button", { name: "Recover original receipt", exact: true });
  await expect(recover).toBeEnabled({ timeout: 20000 });
  let operation;
  await expect
    .poll(
      async () => {
        const result = await page.evaluate(
          async ({ endpoint, token, organizationId, siteId, requestId }) => {
            const response = await fetch(endpoint, {
              method: "POST",
              headers: {
                "Content-Type": "application/json",
                Authorization: `Bearer ${token}`,
                "X-Astrolift-Organization": organizationId,
              },
              body: JSON.stringify({
                query:
                  "query($site:GUID!,$request:String!){astroliftNvkeRequest(siteId:$site,requestId:$request){id version status}}",
                variables: { site: siteId, request: requestId },
              }),
            });
            return response.json();
          },
          { ...config, requestId }
        );
        operation = result.data?.astroliftNvkeRequest;
        return operation?.status;
      },
      { timeout: 20000 }
    )
    .toBe("SUCCEEDED");
  const removed = await page.evaluate(async (value) => {
    const response = await fetch(value.endpoint, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${value.token}`,
        "X-Astrolift-Organization": value.organizationId,
      },
      body: JSON.stringify({
        query:
          "mutation($input:NvkeMetricsAuthorityReviewInput!){removeNvkeMetricsAuthority(input:$input){ok errors{code}}}",
        variables: {
          input: {
            siteId: value.siteId,
            expectedActorId: value.actorId,
            expectedSiteVersion: value.siteVersion,
            expectedVersion: value.authorityVersion,
          },
        },
      }),
    });
    return response.json();
  }, config);
  expect(removed.data?.removeNvkeMetricsAuthority?.ok).toBe(true);
  await page.addInitScript(
    (value) => {
      window.__NVKE_LOCAL_METRICS__ = value;
    },
    { ...config, token: config.readerToken, admin: false, observationId: operation.id }
  );
  await page.reload();
  await expect(page.getByRole("button", { name: "Collect credentials", exact: true })).toHaveCount(
    0
  );
  await expect(recover).toBeEnabled({ timeout: 20000 });
  await recover.click();
  await expect(recover).toHaveCount(0, { timeout: 20000 });
  const pageReply = page.waitForResponse((response) => {
    const request = response.request();
    if (request.method() !== "POST" || request.url() !== config.endpoint) return false;
    const body = request.postDataJSON();
    return body.operationName === "GetNvkeCredentialMetrics" && !!body.variables.after;
  });
  const older = page.getByRole("button", { name: "Older", exact: true }).nth(1);
  await expect(older).toBeEnabled({ timeout: 20000 });
  await older.click();
  const nextPage = await (await pageReply).json();
  expect(nextPage.data?.astroliftNvkeCredentialMetricsPage?.ok).toBe(true);
  await expect(page.getByText("9007199254740993", { exact: true })).toBeVisible({ timeout: 20000 });
  await expect(page.getByText("Collection succeeded", { exact: true }).locator("..")).toContainText(
    "No"
  );
  const searches = page.getByPlaceholder("Search nodes, credentials or metrics");
  const searchReply = page.waitForResponse((response) => {
    const request = response.request();
    if (request.method() !== "POST" || request.url() !== config.endpoint) return false;
    const body = request.postDataJSON();
    return (
      body.operationName === "GetNvkeCredentialMetrics" &&
      body.variables.search === "serving_csr" &&
      !body.variables.after
    );
  });
  await searches.nth(1).fill("serving_csr");
  const searched = await (await searchReply).json();
  expect(searched.data?.astroliftNvkeCredentialMetricsPage?.data?.totalCount).toBe(1);
  await expect(page.getByText("9007199254740993", { exact: true })).toBeVisible();
  const saved = await page.evaluate(() => Object.values(localStorage).join("\n"));
  expect(saved).toContain(requestId);
  expect(saved).not.toContain(config.metricsEndpoint);
  for (const privateValue of ["PRIVATE", "browser-credential-cluster", "cp-1", "9007199254740993"])
    expect(saved).not.toContain(privateValue);
  expect(admissions).toBe(1);
  process.stdout.write(
    JSON.stringify({ requestId, operationId: operation.id, admissions, readOnlyRecovery: true })
  );
} finally {
  await browser.close();
}
