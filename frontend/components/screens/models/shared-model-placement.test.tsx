import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it, vi } from "vitest";
import en from "@/messages/en.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import de from "@/messages/de.json";
import pt from "@/messages/pt-BR.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import zh from "@/messages/zh-Hans.json";
import {
  SharedModelDeploymentScreen,
  type SharedModelDeploymentScreenProps,
} from "./SharedModelDeploymentScreen";
import { sharedModelRequest, type SharedModelRequest } from "./shared-model-form";
import { sharedDeploymentProps } from "./shared-model.fixtures";
const locales = { en, es, fr, de, "pt-BR": pt, ja, ko, "zh-Hans": zh };
function view(props: SharedModelDeploymentScreenProps, locale: keyof typeof locales = "en") {
  return (
    <NextIntlClientProvider locale={locale} messages={locales[locale]} timeZone="UTC">
      <SharedModelDeploymentScreen {...props} />
    </NextIntlClientProvider>
  );
}
type Outcome = Awaited<ReturnType<SharedModelDeploymentScreenProps["onDeploy"]>>;
function pending() {
  let resolve!: (value: Outcome) => void;
  const promise = new Promise<Outcome>((done) => {
    resolve = done;
  });
  return { resolve, promise };
}

describe("shared model placement review", () => {
  it("creates only the complete admitted immutable request, then reports accepted rather than ready", async () => {
    const onDeploy = vi.fn(
      async (_request: SharedModelRequest): Promise<Outcome> => ({
        accepted: true,
        deploymentId: "created-model",
      })
    );
    render(view({ ...sharedDeploymentProps, onDeploy }));
    fireEvent.click(screen.getByRole("button", { name: "Review deployment" }));
    expect(screen.getByRole("alertdialog")).toHaveTextContent(
      "All consumers may temporarily lose access"
    );
    expect(screen.getByRole("alertdialog")).toHaveTextContent(
      "Hardware capacity and model fit remain unverified"
    );
    expect(onDeploy).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Request deployment" }));
    await waitFor(() =>
      expect(onDeploy).toHaveBeenCalledExactlyOnceWith({
        organizationId: "org",
        clusterId: "cluster-one",
        expectedProviderId: "provider-one",
        name: "qwen-shared",
        modelRepo: "Qwen/Qwen3-8B",
        revisionSha: "a".repeat(40),
        computeMode: "gpu",
        cpuRequest: "2",
        memoryRequest: "8Gi",
        gpuCount: 1,
        cpuKvCacheGiB: null,
        allowSubscriptions: true,
        connectionId: null,
        expectedConnectionVersion: null,
        localArtifactId: null,
        expectedArtifactVersion: null,
      })
    );
    expect(
      await screen.findByText(
        "Deployment request accepted. Scheduling and readiness are not yet confirmed."
      )
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open deployment" })).toHaveAttribute(
      "href",
      "/models/shared/created-model"
    );
    expect(screen.getByRole("button", { name: "Review deployment" })).toBeDisabled();
  });
  it("requires explicit compute and a verified runtime for the exact changed request", () => {
    const { rerender } = render(
      view({ ...sharedDeploymentProps, draft: { ...sharedDeploymentProps.draft, computeMode: "" } })
    );
    expect(screen.getByRole("button", { name: "Review deployment" })).toBeDisabled();
    rerender(
      view({
        ...sharedDeploymentProps,
        draft: { ...sharedDeploymentProps.draft, computeMode: "cpu", gpuCount: "0" },
      })
    );
    expect(screen.getByRole("button", { name: "Review deployment" })).toBeDisabled();
    expect(screen.getByLabelText("CPU")).toBeChecked();
    expect(screen.getByText(en.models.shared.placement.unverified)).toBeInTheDocument();
  });
  it("permits an admitted CPU request with zero GPU and its independent KV allocation", async () => {
    const draft = {
      ...sharedDeploymentProps.draft,
      computeMode: "cpu" as const,
      gpuCount: "0",
      cpuKvCacheGiB: "4",
    };
    const request = sharedModelRequest(
      "org",
      { id: "cluster-one", providerId: "provider-one" },
      sharedDeploymentProps.model,
      draft
    );
    const onDeploy = vi.fn(
      async (): Promise<Outcome> => ({ accepted: true, deploymentId: "cpu-model" })
    );
    render(
      view({
        ...sharedDeploymentProps,
        draft,
        onDeploy,
        admission: { ...sharedDeploymentProps.admission!, requestKey: JSON.stringify(request) },
      })
    );
    fireEvent.click(screen.getByRole("button", { name: "Review deployment" }));
    fireEvent.click(screen.getByRole("button", { name: "Request deployment" }));
    await waitFor(() =>
      expect(onDeploy).toHaveBeenCalledExactlyOnceWith({
        ...request,
        cpuKvCacheGiB: 4,
        gpuCount: 0,
        computeMode: "cpu",
      })
    );
  });
  it.each(["cpu", "gpu"] as const)(
    "does not infer %s admission from devices or catalogue metadata",
    (mode) => {
      const onDeploy = vi.fn();
      render(
        view({
          ...sharedDeploymentProps,
          onDeploy,
          draft: {
            ...sharedDeploymentProps.draft,
            computeMode: mode,
            gpuCount: mode === "cpu" ? "0" : "1",
          },
          admission: null,
        })
      );
      expect(screen.getByRole("button", { name: "Review deployment" })).toBeDisabled();
      expect(onDeploy).not.toHaveBeenCalled();
    }
  );
  it("keeps refusal open with the exact draft and no completion", async () => {
    const onDeploy = vi.fn(
      async (): Promise<Outcome> => ({ accepted: false, message: "Configured runtime was removed" })
    );
    render(view({ ...sharedDeploymentProps, onDeploy }));
    fireEvent.click(screen.getByRole("button", { name: "Review deployment" }));
    fireEvent.click(screen.getByRole("button", { name: "Request deployment" }));
    expect(await screen.findByText("Configured runtime was removed")).toBeInTheDocument();
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    expect(screen.getByLabelText("Deployment name")).toHaveValue("qwen-shared");
    expect(screen.queryByRole("link", { name: "Open deployment" })).not.toBeInTheDocument();
  });
  it("does not report completion for an accepted result missing its deployment identity", async () => {
    render(
      view({
        ...sharedDeploymentProps,
        onDeploy: async () => ({ accepted: true, deploymentId: "" }),
      })
    );
    fireEvent.click(screen.getByRole("button", { name: "Review deployment" }));
    fireEvent.click(screen.getByRole("button", { name: "Request deployment" }));
    expect(await screen.findByText(en.models.shared.placement.failed)).toBeInTheDocument();
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Open deployment" })).not.toBeInTheDocument();
  });
  it("clears accepted state permanently when the deployment request changes away and back", async () => {
    const props = {
      ...sharedDeploymentProps,
      onDeploy: async (): Promise<Outcome> => ({ accepted: true, deploymentId: "created-model" }),
    };
    const { rerender } = render(view(props));
    fireEvent.click(screen.getByRole("button", { name: "Review deployment" }));
    fireEvent.click(screen.getByRole("button", { name: "Request deployment" }));
    expect(await screen.findByRole("link", { name: "Open deployment" })).toBeInTheDocument();
    rerender(view({ ...props, draft: { ...props.draft, memoryRequest: "16Gi" } }));
    rerender(view(props));
    expect(screen.queryByRole("link", { name: "Open deployment" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Review deployment" })).toBeEnabled();
  });
  it("permanently invalidates review when resource/admission changes back", () => {
    const onDeploy = vi.fn(),
      props = { ...sharedDeploymentProps, onDeploy };
    const { rerender } = render(view(props));
    fireEvent.click(screen.getByRole("button", { name: "Review deployment" }));
    rerender(view({ ...props, draft: { ...props.draft, memoryRequest: "16Gi" } }));
    rerender(view(props));
    expect(screen.getByRole("button", { name: "Request deployment" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Request deployment" }));
    expect(onDeploy).not.toHaveBeenCalled();
  });
  it("ignores an accepted reply after cluster replacement, without replaying create", async () => {
    const operation = pending(),
      onDeploy = vi.fn(() => operation.promise),
      props = { ...sharedDeploymentProps, onDeploy };
    const { rerender } = render(view(props));
    fireEvent.click(screen.getByRole("button", { name: "Review deployment" }));
    fireEvent.click(screen.getByRole("button", { name: "Request deployment" }));
    await waitFor(() => expect(onDeploy).toHaveBeenCalledTimes(1));
    rerender(view({ ...props, selectedClusterId: "replacement-cluster" }));
    await act(async () => operation.resolve({ accepted: true, deploymentId: "old-model" }));
    expect(screen.queryByRole("link", { name: "Open deployment" })).not.toBeInTheDocument();
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    expect(onDeploy).toHaveBeenCalledTimes(1);
  });
  it("refuses a replaced provider under the same cluster GUID and never revives the old review", () => {
    const onDeploy = vi.fn(),
      props = { ...sharedDeploymentProps, onDeploy };
    const { rerender } = render(view(props));
    fireEvent.click(screen.getByRole("button", { name: "Review deployment" }));
    rerender(
      view({
        ...props,
        clusters: {
          ...props.clusters,
          rows: [{ ...props.clusters.rows[0], providerId: "replacement-provider" }],
        },
      })
    );
    expect(screen.getByRole("button", { name: "Request deployment" })).toBeDisabled();
    rerender(view(props));
    expect(screen.getByRole("button", { name: "Request deployment" })).toBeDisabled();
    expect(onDeploy).not.toHaveBeenCalled();
  });

  it("drops review and late replies across org A→B→A", async () => {
    const operation = pending(),
      onDeploy = vi.fn(() => operation.promise),
      props = { ...sharedDeploymentProps, onDeploy };
    const { rerender } = render(view(props));
    fireEvent.click(screen.getByRole("button", { name: "Review deployment" }));
    fireEvent.click(screen.getByRole("button", { name: "Request deployment" }));
    await waitFor(() => expect(onDeploy).toHaveBeenCalledTimes(1));
    rerender(view({ ...props, organizationId: "other-org" }));
    rerender(view(props));
    await act(async () => operation.resolve({ accepted: true, deploymentId: "old-model" }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Open deployment" })).not.toBeInTheDocument();
    expect(onDeploy).toHaveBeenCalledTimes(1);
  });
  it.each(Object.keys(locales) as (keyof typeof locales)[])(
    "reviews %s without changing IDs/resource units/SHA",
    async (locale) => {
      const onDeploy = vi.fn(
        async (): Promise<Outcome> => ({ accepted: false, message: "VERSION_MISMATCH" })
      );
      const text = locales[locale].models.shared.placement;
      render(view({ ...sharedDeploymentProps, onDeploy }, locale));
      fireEvent.click(screen.getByRole("button", { name: text.review }));
      expect(screen.getByRole("alertdialog")).toHaveTextContent(text.hardwareUnknown);
      expect(screen.getByLabelText(text.memoryRequest)).toHaveValue("8Gi");
      fireEvent.click(screen.getByRole("button", { name: text.deploy }));
      await waitFor(() => expect(onDeploy).toHaveBeenCalledTimes(1));
      expect(onDeploy.mock.calls[0]).toEqual([
        sharedModelRequest(
          "org",
          { id: "cluster-one", providerId: "provider-one" },
          sharedDeploymentProps.model,
          sharedDeploymentProps.draft
        ),
      ]);
      expect(await screen.findByText("VERSION_MISMATCH")).toBeInTheDocument();
    }
  );
});
