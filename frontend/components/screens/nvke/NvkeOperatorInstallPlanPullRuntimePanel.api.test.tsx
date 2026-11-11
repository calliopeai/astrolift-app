import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { afterEach, expect, it, vi } from "vitest";
import en from "@/messages/en.json";
import story from "./NvkeOperatorInstallPlanPullRuntimePanel.api.stories";
import {
  INSTALL_PULL_RUNTIME_PROPS as fixture,
  PULL_RUNTIME_RESULT,
} from "./NvkeOperatorInstallPlanPullRuntimePanel.stories";
import { INSTALL_TREE_PROPS } from "./NvkeOperatorInstallPlanTreePanel.stories";

const ApiReview = story.component;
afterEach(() => {
  delete window.__NVKE_PULL_RUNTIME_API_FIXTURE__;
  vi.unstubAllGlobals();
});
it("uses the actual HTTP link and selected source for explicit lost-read recovery", async () => {
  window.__NVKE_PULL_RUNTIME_API_FIXTURE__ = {
    scope: {
      server: "http://127.0.0.1:55223/__nvke-pull-runtime-api",
      organizationId: fixture.site.id,
      actorId: 41,
    },
    source: INSTALL_TREE_PROPS,
    workload: fixture.workload!,
    binding: fixture.binding!,
    pullAuthority: fixture.pullAuthority!,
    analysis: fixture.analysis!,
    nodePlatforms: fixture.nodePlatforms!,
  };
  const requests: { operationName: string; variables: { input: Record<string, unknown> } }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: unknown, options: RequestInit) => {
      const request = JSON.parse(String(options.body));
      requests.push(request);
      if (requests.length === 1) throw new Error("PRIVATE_TRANSPORT_DETAILS");
      return new Response(
        JSON.stringify({
          data: {
            reviewNvkeOperatorInstallPlanPullRuntime: {
              ok: true,
              errors: [],
              data: PULL_RUNTIME_RESULT,
            },
          },
        }),
        { status: 200, headers: { "content-type": "application/json" } }
      );
    })
  );
  vi.stubGlobal("navigator", {
    locks: {
      request: async (_key: string, _options: unknown, run: () => Promise<unknown>) => run(),
    },
  });
  const element = () => (
    <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
      <ApiReview />
    </NextIntlClientProvider>
  );
  let mounted = render(element());
  expect(requests).toHaveLength(0);
  fireEvent.click(screen.getByRole("button", { name: en.nvke.installPullRuntime.review }));
  await waitFor(() =>
    expect(screen.getByRole("alert").textContent).toBe(en.nvke.installPullRuntime.unconfirmed)
  );
  expect(requests).toHaveLength(1);
  mounted.unmount();
  mounted = render(element());
  expect(requests).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: en.nvke.installPullRuntime.review }));
  await waitFor(() =>
    expect(screen.getByText(PULL_RUNTIME_RESULT.correspondenceDigest)).toBeTruthy()
  );
  expect(requests).toHaveLength(2);
  for (const request of requests) {
    expect(request.operationName).toBe("ReviewNvkeInstallPlanPullRuntime");
    expect(request.variables.input).toMatchObject({
      analysisId: fixture.analysis!.id,
      expectedAnalysisVersion: fixture.analysis!.version,
      analysisDigest: fixture.analysis!.authorityDigest,
      nodePlatformId: fixture.nodePlatforms!.id,
      expectedNodePlatformVersion: fixture.nodePlatforms!.version,
      nodePlatformDigest: fixture.nodePlatforms!.platformDigest,
    });
    expect(request.variables.input).not.toHaveProperty("requestText");
  }
  mounted.unmount();
  mounted = render(element());
  expect(screen.queryByText(PULL_RUNTIME_RESULT.correspondenceDigest)).toBeNull();
  expect(requests).toHaveLength(2);
  mounted.unmount();
});
