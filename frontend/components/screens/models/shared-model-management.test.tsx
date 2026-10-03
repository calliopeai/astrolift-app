import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { PropsWithChildren } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import en from "@/messages/en.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import de from "@/messages/de.json";
import pt from "@/messages/pt-BR.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import zh from "@/messages/zh-Hans.json";
import { SharedModelManagementClient } from "./SharedModelManagementClient";
import { SharedModelManagementPanel } from "./SharedModelManagementPanel";
import { sharedModelManagementProps } from "./shared-model-management.fixtures";
import type { ClusterModelFieldsFragment } from "@/graphql/__generated__/operations";
const locales = { en, es, fr, de, "pt-BR": pt, ja, ko, "zh-Hans": zh };
const org = vi.hoisted(() => ({ id: "00000000-0000-4000-8000-000000000001" }));
vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({ org: { id: org.id }, loading: false, error: null }),
}));
const model: ClusterModelFieldsFragment = {
  ...sharedModelManagementProps.model,
  organizationId: org.id,
  id: "00000000-0000-4000-8000-000000000002",
  clusterId: "00000000-0000-4000-8000-000000000003",
  providerId: "00000000-0000-4000-8000-000000000004",
};
type Request = { operationName: string; variables: Record<string, unknown> };
let requests: Request[], transport: (request: Request) => Promise<Response>;
function response(data: Record<string, unknown>) {
  return new Response(JSON.stringify({ data }), {
    headers: { "Content-Type": "application/json" },
  });
}
function fixture(request: Request): Response {
  if (request.operationName === "Me")
    return response({
      me: {
        id: "00000000-0000-4000-8000-000000000009",
        profile: { id: "00000000-0000-4000-8000-000000000008", username: "owner" },
        modules: [
          {
            key: "models",
            enabled: true,
            canView: true,
            canCreate: true,
            canManage: true,
            canRun: true,
          },
        ],
      },
    });
  if (request.operationName === "GetClusterModelRuntimeAdmission")
    return response({
      clusterModelRuntimeAdmission: {
        eligible: true,
        reason: null,
        runtimeVersion: "0.15.1",
        architecture: "amd64",
        hardwareAdmission: "operator_declared",
      },
    });
  const input = request.variables.input as Record<string, unknown>,
    update = request.operationName === "UpdateClusterModel";
  if (!update && request.operationName !== "DeprovisionClusterModel")
    throw new Error(`Unexpected ${request.operationName}`);
  return response({
    [update ? "updateClusterModel" : "deprovisionClusterModel"]: {
      ok: true,
      errors: [],
      data: {
        ...model,
        version: 7,
        status: update ? "updating" : "deprovisioning",
        operationId: "SharedModelReconcileWorkflow-actual-model-4",
        operationStartedAt: "2026-09-30T16:00:00Z",
        operationCompletedAt: null,
        ready: false,
        readinessObservedAt: null,
        readinessGeneration: null,
        desiredSubscriptionRevision: 4,
        desiredResources: update
          ? {
              ...model.desiredResources,
              cpuRequest: input.cpuRequest,
              memoryRequest: input.memoryRequest,
              gpuCount: input.gpuCount,
              cpuKvCacheGiB: input.cpuKvCacheGiB,
            }
          : model.desiredResources,
        subscriptionsEnabled: update ? input.allowSubscriptions : model.subscriptionsEnabled,
      },
    },
  });
}
function wrapper(locale: keyof typeof locales = "en") {
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new HttpLink({
      uri: "http://control-plane.test/app/gql/config/",
      fetch: vi.fn(async (_uri, options) => {
        const request: Request = JSON.parse(String(options?.body));
        requests.push(request);
        return transport(request);
      }),
    }),
  });
  return function Provider({ children }: PropsWithChildren) {
    return (
      <NextIntlClientProvider locale={locale} messages={locales[locale]} timeZone="UTC">
        <ApolloProvider client={client}>{children}</ApolloProvider>
      </NextIntlClientProvider>
    );
  };
}
function client(current = model, refresh = vi.fn(), blocked = false) {
  return <SharedModelManagementClient model={current} blocked={blocked} onRefresh={refresh} />;
}
function writes() {
  return requests.filter((request) =>
    ["UpdateClusterModel", "DeprovisionClusterModel"].includes(request.operationName)
  );
}
beforeEach(() => {
  org.id = model.organizationId;
  requests = [];
  transport = async (request) => fixture(request);
});
async function reviewUpdate() {
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: en.models.shared.management.reviewUpdate })
    ).toBeEnabled()
  );
  fireEvent.change(screen.getByLabelText(en.models.shared.placement.cpuRequest), {
    target: { value: "4" },
  });
  fireEvent.change(screen.getByLabelText(en.models.shared.placement.memoryRequest), {
    target: { value: "16Gi" },
  });
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: en.models.shared.management.reviewUpdate })
    ).toBeEnabled()
  );
  fireEvent.click(screen.getByRole("button", { name: en.models.shared.management.reviewUpdate }));
}

describe("real shared model management boundary", () => {
  it("retries a failed capability read without issuing any management write", async () => {
    let failed = true;
    transport = async (request) =>
      request.operationName === "Me" && failed
        ? new Response(
            JSON.stringify({ errors: [{ message: "Management manifest unavailable" }] }),
            { headers: { "Content-Type": "application/json" } }
          )
        : fixture(request);
    render(client(), { wrapper: wrapper() });
    expect(await screen.findByText("Management manifest unavailable")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: en.models.shared.management.reviewDelete })
    ).toBeDisabled();
    failed = false;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: en.models.shared.management.reviewUpdate })
      ).toBeEnabled()
    );
    expect(requests.filter((request) => request.operationName === "Me")).toHaveLength(2);
    expect(writes()).toHaveLength(0);
  });
  it("updates persisted CPU with independent KV allocation and zero GPU without changing compute mode", async () => {
    const cpu = {
      ...model,
      computeMode: "cpu",
      desiredResources: { ...model.desiredResources, gpuCount: 0, cpuKvCacheGiB: 4 },
    };
    transport = async (request) => {
      const result = await fixture(request).json();
      if (request.operationName === "UpdateClusterModel")
        result.data.updateClusterModel.data.computeMode = "cpu";
      return response(result.data);
    };
    render(client(cpu), { wrapper: wrapper() });
    await reviewUpdate();
    fireEvent.click(
      screen.getByRole("button", { name: en.models.shared.management.confirmUpdate })
    );
    expect(await screen.findByText(en.models.shared.management.acceptedUpdate)).toBeInTheDocument();
    expect(writes()[0].variables.input).toMatchObject({ gpuCount: 0, cpuKvCacheGiB: 4 });
    expect(
      requests
        .filter((request) => request.operationName === "GetClusterModelRuntimeAdmission")
        .at(-1)?.variables.input
    ).toMatchObject({ computeMode: "cpu", gpuCount: 0, cpuKvCacheGiB: 4 });
  });
  it.each(["foreign", "missing"])(
    "refuses %s deprovision reply without claiming removal",
    async (kind) => {
      transport = async (request) => {
        if (request.operationName !== "DeprovisionClusterModel") return fixture(request);
        const result = await fixture(request).json();
        if (kind === "missing") result.data.deprovisionClusterModel.data = null;
        else
          result.data.deprovisionClusterModel.data.clusterId =
            "00000000-0000-4000-8000-000000000099";
        return response(result.data);
      };
      render(client(), { wrapper: wrapper() });
      await waitFor(() =>
        expect(
          screen.getByRole("button", { name: en.models.shared.management.reviewDelete })
        ).toBeEnabled()
      );
      fireEvent.click(
        screen.getByRole("button", { name: en.models.shared.management.reviewDelete })
      );
      fireEvent.click(
        screen.getByRole("button", { name: en.models.shared.management.confirmDelete })
      );
      expect(await screen.findByText(en.models.shared.management.failed)).toBeInTheDocument();
      expect(screen.getByRole("alertdialog")).toBeInTheDocument();
      expect(
        screen.queryByText(en.models.shared.management.acceptedDelete)
      ).not.toBeInTheDocument();
      expect(writes()).toHaveLength(1);
    }
  );
  it("does not revive a late update after the resource draft changes away and back", async () => {
    let release!: (value: Response) => void;
    transport = async (request) =>
      request.operationName === "UpdateClusterModel"
        ? new Promise<Response>((resolve) => {
            release = resolve;
          })
        : fixture(request);
    const refresh = vi.fn();
    render(client(model, refresh), { wrapper: wrapper() });
    await reviewUpdate();
    fireEvent.click(
      screen.getByRole("button", { name: en.models.shared.management.confirmUpdate })
    );
    await waitFor(() => expect(writes()).toHaveLength(1));
    fireEvent.change(screen.getByLabelText(en.models.shared.placement.memoryRequest), {
      target: { value: "32Gi" },
    });
    fireEvent.change(screen.getByLabelText(en.models.shared.placement.memoryRequest), {
      target: { value: "16Gi" },
    });
    await act(async () => release(fixture(writes()[0])));
    expect(screen.queryByText(en.models.shared.management.acceptedUpdate)).not.toBeInTheDocument();
    expect(refresh).not.toHaveBeenCalled();
    expect(writes()).toHaveLength(1);
    expect(
      screen.getByRole("button", { name: en.models.shared.management.confirmUpdate })
    ).toBeDisabled();
  });
  it("admits the exact persisted placement/compute and edited resource request before a versioned update", async () => {
    const refresh = vi.fn();
    render(client(model, refresh), { wrapper: wrapper() });
    await reviewUpdate();
    expect(writes()).toHaveLength(0);
    expect(screen.getByRole("alertdialog")).toHaveTextContent(
      en.models.shared.management.restartImpact
    );
    fireEvent.click(
      screen.getByRole("button", { name: en.models.shared.management.confirmUpdate })
    );
    expect(await screen.findByText(en.models.shared.management.acceptedUpdate)).toBeInTheDocument();
    expect(writes()[0]).toMatchObject({
      operationName: "UpdateClusterModel",
      variables: {
        input: {
          organizationId: model.organizationId,
          id: model.id,
          expectedClusterId: model.clusterId,
          expectedProviderId: model.providerId,
          ifMatchVersion: model.version,
          cpuRequest: "4",
          memoryRequest: "16Gi",
          gpuCount: 1,
          cpuKvCacheGiB: null,
          allowSubscriptions: true,
        },
      },
    });
    expect(writes()).toHaveLength(1);
    expect(refresh).toHaveBeenCalledOnce();
    expect(
      requests
        .filter((request) => request.operationName === "GetClusterModelRuntimeAdmission")
        .at(-1)?.variables.input
    ).toEqual({
      organizationId: model.organizationId,
      clusterId: model.clusterId,
      expectedProviderId: model.providerId,
      name: model.name,
      modelRepo: model.modelRepo,
      revisionSha: model.revisionSha,
      computeMode: "gpu",
      cpuRequest: "4",
      memoryRequest: "16Gi",
      gpuCount: 1,
      cpuKvCacheGiB: null,
      allowSubscriptions: true,
      connectionId: null,
      expectedConnectionVersion: null,
      localArtifactId: null,
      expectedArtifactVersion: null,
    });
    expect(screen.queryByText(en.models.shared.management.completedUpdate)).not.toBeInTheDocument();
  });
  it("allows retained-data cleanup of an idle failed model without configured runtime admission", async () => {
    transport = async (request) =>
      request.operationName === "GetClusterModelRuntimeAdmission"
        ? response({
            clusterModelRuntimeAdmission: {
              eligible: false,
              reason: "Runtime is not configured",
              runtimeVersion: null,
              architecture: null,
              hardwareAdmission: "unknown",
            },
          })
        : fixture(request);
    render(client({ ...model, status: "failed", runtimeSupported: null }), { wrapper: wrapper() });
    expect(await screen.findByText("Runtime is not configured")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: en.models.shared.management.reviewUpdate })
    ).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: en.models.shared.management.reviewDelete }));
    expect(screen.getByRole("alertdialog")).toHaveTextContent(
      en.models.shared.management.deleteRetained
    );
    fireEvent.click(
      screen.getByRole("button", { name: en.models.shared.management.confirmDelete })
    );
    expect(await screen.findByText(en.models.shared.management.acceptedDelete)).toBeInTheDocument();
    expect(writes()).toHaveLength(1);
    expect(writes()[0]).toMatchObject({
      operationName: "DeprovisionClusterModel",
      variables: {
        input: {
          organizationId: model.organizationId,
          id: model.id,
          expectedClusterId: model.clusterId,
          expectedProviderId: model.providerId,
          ifMatchVersion: model.version,
          deleteData: false,
        },
      },
    });
  });
  it.each(["accepted", "refused"])(
    "preserves an actual %s deletion reply when resource admission finishes during delivery",
    async (outcome) => {
      let finishAdmission: (() => void) | undefined;
      let finishDeletion: (() => void) | undefined;
      transport = async (request) => {
        if (request.operationName === "GetClusterModelRuntimeAdmission")
          return new Promise<Response>((resolve) => {
            finishAdmission = () => resolve(fixture(request));
          });
        if (request.operationName === "DeprovisionClusterModel")
          return new Promise<Response>((resolve) => {
            finishDeletion = () =>
              resolve(
                outcome === "accepted"
                  ? fixture(request)
                  : response({
                      deprovisionClusterModel: {
                        ok: false,
                        data: null,
                        errors: [{ code: "CONFLICT", message: "Subscriptions remain active." }],
                      },
                    })
              );
          });
        return fixture(request);
      };
      const refresh = vi.fn();
      render(client(model, refresh), { wrapper: wrapper() });
      await waitFor(() => {
        expect(finishAdmission).toBeDefined();
        expect(
          screen.getByRole("button", { name: en.models.shared.management.reviewDelete })
        ).toBeEnabled();
      });
      fireEvent.click(
        screen.getByRole("button", { name: en.models.shared.management.reviewDelete })
      );
      fireEvent.click(
        screen.getByRole("button", { name: en.models.shared.management.confirmDelete })
      );
      await waitFor(() => expect(finishDeletion).toBeDefined());
      await act(async () => finishAdmission!());
      await waitFor(() =>
        expect(screen.queryByText(en.models.shared.placement.verifying)).not.toBeInTheDocument()
      );
      await act(async () => finishDeletion!());
      if (outcome === "accepted") {
        expect(
          await screen.findByText(en.models.shared.management.acceptedDelete)
        ).toBeInTheDocument();
        expect(refresh).toHaveBeenCalledTimes(1);
      } else {
        expect(
          await screen.findByText("CONFLICT: Subscriptions remain active.")
        ).toBeInTheDocument();
        expect(screen.getByRole("alertdialog")).toBeInTheDocument();
        expect(refresh).not.toHaveBeenCalled();
      }
      expect(screen.queryByText(en.models.shared.management.changed)).not.toBeInTheDocument();
      expect(writes()).toHaveLength(1);
    }
  );
  it("shows exact pending-subscription deletion refusal without claiming removal", async () => {
    transport = async (request) =>
      request.operationName === "DeprovisionClusterModel"
        ? response({
            deprovisionClusterModel: {
              ok: false,
              data: null,
              errors: [
                {
                  code: "PRECONDITION",
                  message:
                    "Revoke every subscription and confirm reconciliation before deleting this model.",
                  field: null,
                  currentVersion: null,
                  requestedVersion: null,
                  requiresAttestation: false,
                  supportedMethods: [],
                },
              ],
            },
          })
        : fixture(request);
    render(client(), { wrapper: wrapper() });
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: en.models.shared.management.reviewDelete })
      ).toBeEnabled()
    );
    fireEvent.click(screen.getByRole("button", { name: en.models.shared.management.reviewDelete }));
    fireEvent.click(
      screen.getByRole("button", { name: en.models.shared.management.confirmDelete })
    );
    expect(
      await screen.findByText(
        "PRECONDITION: Revoke every subscription and confirm reconciliation before deleting this model."
      )
    ).toBeInTheDocument();
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    expect(screen.queryByText(en.models.shared.management.acceptedDelete)).not.toBeInTheDocument();
    expect(writes()).toHaveLength(1);
  });
  it.each(["reader", "missing", "denied"])(
    "does not use Models visibility as management authority for %s",
    async (kind) => {
      transport = async (request) => {
        if (request.operationName !== "Me") return fixture(request);
        if (kind === "denied")
          return new Response(
            JSON.stringify({ errors: [{ message: "Management manifest denied" }] }),
            { headers: { "Content-Type": "application/json" } }
          );
        if (kind === "missing") return response({ me: null });
        const result = await fixture(request).json();
        result.data.me.modules[0].canManage = false;
        return response(result.data);
      };
      render(client(), { wrapper: wrapper() });
      expect(
        await screen.findByText(
          kind === "denied" ? "Management manifest denied" : en.models.shared.management.readOnly
        )
      ).toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: en.models.shared.management.reviewDelete })
      ).toBeDisabled();
      expect(
        screen.getByRole("button", { name: en.models.shared.management.reviewUpdate })
      ).toBeDisabled();
      expect(requests.map((request) => request.operationName)).toEqual(["Me"]);
    }
  );
  it.each(["mixed", "missing", "foreign", "wrong_resources", "completed"])(
    "retains update review and draft for %s reply",
    async (kind) => {
      transport = async (request) => {
        if (request.operationName !== "UpdateClusterModel") return fixture(request);
        const result = await fixture(request).json(),
          envelope = result.data.updateClusterModel;
        if (kind === "mixed")
          envelope.errors = [
            {
              code: "VERSION_MISMATCH",
              message: "Refresh the deployment",
              field: "ifMatchVersion",
              currentVersion: 9,
              requestedVersion: 5,
              requiresAttestation: false,
              supportedMethods: [],
            },
          ];
        if (kind === "missing") envelope.data = null;
        if (kind === "foreign") envelope.data.providerId = "00000000-0000-4000-8000-000000000099";
        if (kind === "wrong_resources") envelope.data.desiredResources.memoryRequest = "32Gi";
        if (kind === "completed") envelope.data.operationCompletedAt = "2026-09-30T16:00:01Z";
        return response(result.data);
      };
      render(client(), { wrapper: wrapper() });
      await reviewUpdate();
      fireEvent.click(
        screen.getByRole("button", { name: en.models.shared.management.confirmUpdate })
      );
      expect(
        await screen.findByText(
          kind === "mixed"
            ? "VERSION_MISMATCH: Refresh the deployment"
            : en.models.shared.management.failed
        )
      ).toBeInTheDocument();
      expect(screen.getByRole("alertdialog")).toBeInTheDocument();
      expect(screen.getByLabelText(en.models.shared.placement.memoryRequest)).toHaveValue("16Gi");
      expect(
        screen.queryByText(en.models.shared.management.acceptedUpdate)
      ).not.toBeInTheDocument();
      expect(writes()).toHaveLength(1);
    }
  );
  it("preserves known acceptance when the independent detail refresh fails", async () => {
    const refresh = vi.fn(() => {
      throw new Error("Read refresh failed");
    });
    render(client(model, refresh), { wrapper: wrapper() });
    await reviewUpdate();
    fireEvent.click(
      screen.getByRole("button", { name: en.models.shared.management.confirmUpdate })
    );
    expect(await screen.findByText(en.models.shared.management.acceptedUpdate)).toBeInTheDocument();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(writes()).toHaveLength(1);
    expect(refresh).toHaveBeenCalledOnce();
  });
  it("permanently ignores a late update after version A→B→A", async () => {
    let release!: (value: Response) => void;
    transport = async (request) =>
      request.operationName === "UpdateClusterModel"
        ? new Promise<Response>((resolve) => {
            release = resolve;
          })
        : fixture(request);
    const refresh = vi.fn(),
      { rerender } = render(client(model, refresh), { wrapper: wrapper() });
    await reviewUpdate();
    fireEvent.click(
      screen.getByRole("button", { name: en.models.shared.management.confirmUpdate })
    );
    await waitFor(() => expect(writes()).toHaveLength(1));
    rerender(client({ ...model, version: 6 }, refresh));
    rerender(client(model, refresh));
    await act(async () => release(fixture(writes()[0])));
    expect(screen.queryByText(en.models.shared.management.acceptedUpdate)).not.toBeInTheDocument();
    expect(refresh).not.toHaveBeenCalled();
    expect(writes()).toHaveLength(1);
    expect(
      screen.getByRole("button", { name: en.models.shared.management.confirmUpdate })
    ).toBeDisabled();
  });
  it("does not revive a late deletion after the target version changes away and back", async () => {
    let release!: (value: Response) => void;
    transport = async (request) =>
      request.operationName === "DeprovisionClusterModel"
        ? new Promise<Response>((resolve) => {
            release = resolve;
          })
        : fixture(request);
    const refresh = vi.fn(),
      { rerender } = render(client(model, refresh), { wrapper: wrapper() });
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: en.models.shared.management.reviewDelete })
      ).toBeEnabled()
    );
    fireEvent.click(screen.getByRole("button", { name: en.models.shared.management.reviewDelete }));
    fireEvent.click(
      screen.getByRole("button", { name: en.models.shared.management.confirmDelete })
    );
    await waitFor(() => expect(writes()).toHaveLength(1));
    rerender(client({ ...model, version: 6 }, refresh));
    rerender(client(model, refresh));
    await act(async () => release(fixture(writes()[0])));
    expect(screen.queryByText(en.models.shared.management.acceptedDelete)).not.toBeInTheDocument();
    expect(refresh).not.toHaveBeenCalled();
    expect(writes()).toHaveLength(1);
    expect(
      screen.getByRole("button", { name: en.models.shared.management.confirmDelete })
    ).toBeDisabled();
  });
  it("keeps pending operations unavailable for further management", async () => {
    render(client({ ...model, status: "updating" }), { wrapper: wrapper() });
    await screen.findByText(en.models.shared.management.blocked);
    expect(
      screen.getByRole("button", { name: en.models.shared.management.reviewDelete })
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: en.models.shared.management.reviewUpdate })
    ).toBeDisabled();
    expect(writes()).toHaveLength(0);
  });
  it.each(Object.keys(locales) as (keyof typeof locales)[])(
    "reviews retained-data removal in %s with unchanged placement/version inputs",
    async (locale) => {
      const onDelete = vi.fn(async () => ({
          accepted: true as const,
          operationId: "actual-operation",
        })),
        text = locales[locale].models.shared.management;
      render(<SharedModelManagementPanel {...sharedModelManagementProps} onDelete={onDelete} />, {
        wrapper: wrapper(locale),
      });
      fireEvent.click(screen.getByRole("button", { name: text.reviewDelete }));
      expect(screen.getByRole("alertdialog")).toHaveTextContent(text.deleteRetained);
      fireEvent.click(screen.getByRole("button", { name: text.confirmDelete }));
      expect(await screen.findByText(text.acceptedDelete)).toBeInTheDocument();
      expect(onDelete).toHaveBeenCalledExactlyOnceWith({
        organizationId: "org",
        id: "shared-model",
        expectedClusterId: "cluster-one",
        expectedProviderId: "provider-one",
        ifMatchVersion: 5,
        deleteData: false,
      });
    }
  );
  it("enables another update only after the matching operation completion is actually read", async () => {
    const props = {
        ...sharedModelManagementProps,
        onUpdate: async () => ({ accepted: true as const, operationId: "accepted-operation" }),
      },
      { rerender } = render(<SharedModelManagementPanel {...props} />, { wrapper: wrapper() });
    fireEvent.click(screen.getByRole("button", { name: en.models.shared.management.reviewUpdate }));
    fireEvent.click(
      screen.getByRole("button", { name: en.models.shared.management.confirmUpdate })
    );
    expect(await screen.findByText(en.models.shared.management.acceptedUpdate)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: en.models.shared.management.reviewUpdate })
    ).toBeDisabled();
    rerender(
      <SharedModelManagementPanel
        {...props}
        model={{
          ...props.model,
          version: 7,
          operationId: "accepted-operation",
          operationCompletedAt: "2026-09-30T16:01:00Z",
        }}
      />
    );
    expect(screen.getByText(en.models.shared.management.completedUpdate)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: en.models.shared.management.reviewUpdate })
    ).toBeEnabled();
  });
});
