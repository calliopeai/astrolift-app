import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DEPLOY_RUNNING } from "@/components/screens/apps/deployments/app-deployments-logs.fixtures";
import { REDEPLOY_APP } from "@/graphql/lifecycle/lifecycle.mutations";
import { GET_DEPLOYMENT } from "@/graphql/lifecycle/lifecycle.queries";

import { useDeploymentDetail } from "./use-deployment-detail";

const mocks = vi.hoisted(() => ({
  redeploy: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
  missing: false,
}));

vi.mock("@apollo/client/react", () => ({
  useQuery: (query: unknown) => ({
    data:
      query === GET_DEPLOYMENT && !mocks.missing
        ? { astroliftDeployment: DEPLOY_RUNNING }
        : undefined,
    loading: false,
    refetch: vi.fn(),
  }),
  useMutation: (query: unknown) => [
    query === REDEPLOY_APP ? mocks.redeploy : vi.fn(),
    { loading: false },
  ],
  useSubscription: vi.fn(),
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));
vi.mock("sonner", () => ({ toast: { success: mocks.success, error: mocks.error } }));
vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({ can: () => true }),
}));

beforeEach(() => {
  vi.clearAllMocks();
  mocks.missing = false;
  mocks.redeploy.mockResolvedValue({
    data: { redeployApp: { ok: true, errors: [], data: DEPLOY_RUNNING } },
  });
});

describe("deployment detail redeploy", () => {
  it("uses DeploymentByIdInput to redeploy the selected historical deployment", async () => {
    const { result } = renderHook(() => useDeploymentDetail(DEPLOY_RUNNING.id));
    await act(() => result.current.onRedeploy());
    expect(mocks.redeploy).toHaveBeenCalledWith({
      variables: { input: { id: DEPLOY_RUNNING.id } },
    });
    expect(mocks.success).toHaveBeenCalled();
    expect(mocks.error).not.toHaveBeenCalled();
  });

  it("surfaces a rejected mutation without leaving the direct action promise rejected", async () => {
    mocks.redeploy.mockResolvedValue({
      data: {
        redeployApp: {
          ok: false,
          errors: [{ code: "denied", message: "Deploy permission denied" }],
          data: null,
        },
      },
    });
    const { result } = renderHook(() => useDeploymentDetail(DEPLOY_RUNNING.id));
    await act(() => result.current.onRedeploy());
    expect(mocks.error).toHaveBeenCalledWith("Deploy permission denied");
    expect(mocks.success).not.toHaveBeenCalled();
  });

  it("surfaces transport failures", async () => {
    mocks.redeploy.mockRejectedValue(new Error("Connection lost"));
    const { result } = renderHook(() => useDeploymentDetail(DEPLOY_RUNNING.id));
    await act(() => result.current.onRedeploy());
    expect(mocks.error).toHaveBeenCalledWith("Connection lost");
  });

  it("uses a translated failure when the response has no mutation result", async () => {
    mocks.redeploy.mockResolvedValue({ data: null });
    const { result } = renderHook(() => useDeploymentDetail(DEPLOY_RUNNING.id));
    await act(() => result.current.onRedeploy());
    expect(mocks.error).toHaveBeenCalledWith("redeployFailed");
  });

  it("does not redeploy while the deployment is unavailable", async () => {
    mocks.missing = true;
    const { result } = renderHook(() => useDeploymentDetail(DEPLOY_RUNNING.id));
    await act(() => result.current.onRedeploy());
    expect(mocks.redeploy).not.toHaveBeenCalled();
  });
});
