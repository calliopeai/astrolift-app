import { renderWithIntl as render } from "@/test/render-with-intl";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  CI_SETUP_APAC,
  CI_SETUP_EU,
  CI_SETUP_UNAVAILABLE,
} from "./app-ci-observability-section.fixtures";
import { renderWorkflowYaml, resolveProviderCiMeta } from "./ci-setup-meta";
import { CiSetupSectionView } from "./CiSetupSection";

vi.mock("@/components/Can", () => ({ Can: () => null }));

describe("CI workflow region", () => {
  it.each([CI_SETUP_EU, CI_SETUP_APAC])(
    "copies the backend workflow verbatim for $registryUri",
    async (fixture) => {
      const writeText = vi.fn().mockResolvedValue(undefined);
      Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
      render(<CiSetupSectionView {...fixture} />);
      fireEvent.click(screen.getByText("Managed CI workflow"));
      fireEvent.click(screen.getByRole("button", { name: "Copy workflow YAML" }));
      await waitFor(() =>
        expect(writeText).toHaveBeenCalledWith(fixture.ciWorkflowSyncStatus!.renderedText)
      );
      expect(writeText.mock.calls[0][0]).not.toContain("us-west-2");
      expect(writeText.mock.calls[0][0]).toContain('branches: ["release/eu"]');
    }
  );

  it("refuses a reference when the backend cannot validate the AWS configuration", () => {
    render(<CiSetupSectionView {...CI_SETUP_UNAVAILABLE} />);
    fireEvent.click(screen.getByText("Managed CI workflow"));
    expect(screen.getByRole("button", { name: "Copy workflow YAML" })).toBeDisabled();
    expect(screen.getByText(/Workflow unavailable/)).toBeInTheDocument();
  });

  it.each(["aws", "", "unknown", "constructor", "__proto__"])(
    "does not invent a workflow for provider %s",
    (providerSlug) => {
      expect(renderWorkflowYaml(resolveProviderCiMeta(providerSlug), { providerSlug })).toBeNull();
    }
  );

  it.each(["constructor", "__proto__"])("uses neutral metadata for opaque provider %s", (slug) => {
    const meta = resolveProviderCiMeta(slug);
    expect(meta.pushCredentialEnv).toBe("ASTROLIFT_PUSH_ROLE_ARN");
    expect(typeof meta.renderWorkflowSteps).toBe("function");
    render(<CiSetupSectionView {...CI_SETUP_EU} providerPluginSlug={slug} />);
    fireEvent.click(screen.getByText("Managed CI workflow"));
    expect(screen.getByText((_, element) => element?.tagName === "PRE").textContent).toBe(
      CI_SETUP_EU.ciWorkflowSyncStatus!.renderedText
    );
  });

  it.each([".gitlab-ci.yml", "bitbucket-pipelines.yml", ".gitea/workflows/astrolift-ci.yml"])(
    "displays the backend source-host workflow at %s without relabeling it GitHub",
    (path) => {
      const renderedText = `# host-specific workflow at ${path}\n`;
      render(
        <CiSetupSectionView
          {...CI_SETUP_EU}
          ciWorkflowSyncStatus={{ ...CI_SETUP_EU.ciWorkflowSyncStatus!, path, renderedText }}
        />
      );
      fireEvent.click(screen.getByText("Managed CI workflow"));
      expect(screen.getAllByText(path).length).toBeGreaterThan(0);
      expect(screen.getByText(renderedText.trim())).toBeInTheDocument();
      expect(screen.queryByText("Reference GitHub Actions workflow")).not.toBeInTheDocument();
    }
  );

  it.each([
    ["gcp", "google-github-actions/auth@v2"],
    ["azure", "azure/login@v2"],
    ["k8s_native", "docker/login-action@v3"],
  ])("preserves %s's reference steps and the app's deploy branch", (providerSlug, action) => {
    const deployBranch = 'release/"eu", other]';
    const workflow = renderWorkflowYaml(resolveProviderCiMeta(providerSlug), {
      providerSlug,
      deployBranch,
      renderedText: "AWS-only backend body must not replace a provider reference",
    });
    expect(workflow).toContain(action);
    expect(workflow).toContain(`branches: [${JSON.stringify(deployBranch)}]`);
    expect(workflow).not.toContain("aws-actions");
    expect(workflow).not.toContain("AWS-only");
  });
});
