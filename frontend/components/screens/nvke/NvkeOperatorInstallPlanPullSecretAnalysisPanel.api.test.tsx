import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { afterEach, expect, it, vi } from "vitest";
import en from "@/messages/en.json";
import story from "./NvkeOperatorInstallPlanPullSecretAnalysisPanel.api.stories";
import { INSTALL_PULL_SECRET_ANALYSIS_PROPS as fixture } from "./NvkeOperatorInstallPlanPullSecretAnalysisPanel.stories";
import { INSTALL_TREE_PROPS } from "./NvkeOperatorInstallPlanTreePanel.stories";

const ApiRecovery = story.component;
afterEach(() => {
  delete window.__NVKE_PULL_SECRET_ANALYSIS_API_FIXTURE__;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});
it("wires the exact observed parent into explicit preparation and cold browser review", async () => {
  const errors = vi.spyOn(console, "error");
  localStorage.clear();
  window.__NVKE_PULL_SECRET_ANALYSIS_API_FIXTURE__ = {
    scope: {
      server: "http://127.0.0.1:55222/__nvke-pull-secret-analysis-api",
      organizationId: "123e4567-e89b-12d3-a456-426614174000",
      actorId: 41,
    },
    source: INSTALL_TREE_PROPS,
    workload: fixture.workload!,
    binding: fixture.binding!,
    pullAuthority: fixture.pullAuthority!,
  };
  const requests: { operationName: string; variables: { input: Record<string, unknown> } }[] = [];
  const fetch = vi.fn(async (_url: unknown, options: RequestInit) => {
    const request = JSON.parse(String(options.body));
    requests.push(request);
    if (request.operationName === "PrepareNvkeInstallPlanPullSecretAnalysis")
      throw new Error("Owned lost response");
    return new Response(
      JSON.stringify({
        data: {
          reviewNvkeOperatorInstallPlanPullSecretAnalysis: {
            ok: true,
            errors: [],
            data: { ...fixture.artifacts!, imageUseCount: 1 },
          },
        },
      }),
      { status: 200, headers: { "content-type": "application/json" } }
    );
  });
  vi.stubGlobal("fetch", fetch);
  vi.stubGlobal("navigator", {
    locks: {
      request: async (_key: string, _options: unknown, run: () => Promise<unknown>) => run(),
    },
  });
  const panel = () => (
    <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
      <ApiRecovery />
    </NextIntlClientProvider>
  );
  let mounted = render(panel());
  await waitFor(() =>
    expect(screen.getByRole("button", { name: en.nvke.installImmutable.prepare })).toBeEnabled()
  );
  expect(fetch).not.toHaveBeenCalled();
  const id = fixture.artifacts!.id;
  fireEvent.change(screen.getByLabelText(en.nvke.installTree.artifactId), {
    target: { value: id },
  });
  const imageSelections = [
    {
      podKey: "private/pod",
      imagePointer: "/spec/containers/0/image",
      ensureSecretPulledImages: true,
    },
  ];
  fireEvent.change(screen.getByLabelText(en.nvke.installPullSecretAnalysis.imageSelections), {
    target: { value: JSON.stringify(imageSelections) },
  });
  fireEvent.click(screen.getByRole("button", { name: en.nvke.installImmutable.prepare }));
  await waitFor(() => expect(requests).toHaveLength(1));
  await waitFor(() =>
    expect(screen.getByRole("button", { name: en.nvke.installImmutable.prepare })).toBeDisabled()
  );
  expect(requests[0].variables.input).toMatchObject({
    pullAuthorityId: fixture.pullAuthority!.id,
    expectedPullAuthorityVersion: fixture.pullAuthority!.version,
    pullAuthorityDigest: fixture.pullAuthority!.authorityDigest,
  });
  expect(JSON.parse(String(requests[0].variables.input.requestText)).imageSelections).toEqual(
    imageSelections
  );
  expect(JSON.stringify(localStorage)).not.toContain("private/pod");
  mounted.unmount();
  mounted = render(panel());
  expect(requests).toHaveLength(1);
  await waitFor(() =>
    expect(screen.getByRole("button", { name: en.nvke.installImmutable.review })).toBeEnabled()
  );
  fireEvent.click(screen.getByRole("button", { name: en.nvke.installImmutable.review }));
  await waitFor(() => expect(requests).toHaveLength(2));
  expect(requests[1].variables.input).toMatchObject({
    artifactId: id,
    pullAuthorityId: fixture.pullAuthority!.id,
  });
  expect(requests[1].variables.input).not.toHaveProperty("requestText");
  await waitFor(() => expect(screen.getByText(fixture.artifacts!.authorityDigest)).toBeTruthy());
  expect(errors.mock.calls.flat().join(" ")).not.toContain("uncontrolled");
  mounted.unmount();
});
