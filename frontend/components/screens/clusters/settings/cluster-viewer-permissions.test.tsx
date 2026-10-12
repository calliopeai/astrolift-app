import { renderHook } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import messages from "@/messages/en.json";

import { useClusterSettings } from "./use-cluster-settings";

const state = vi.hoisted(() => ({ cluster: null as Record<string, unknown> | null }));

vi.mock("@apollo/client/react", () => ({
  useQuery: () => ({
    data: state.cluster ? { astroliftCluster: state.cluster } : undefined,
    previousData: undefined,
    error: undefined,
    loading: false,
    refetch: vi.fn(),
    startPolling: vi.fn(),
    stopPolling: vi.fn(),
  }),
  useMutation: () => [vi.fn(), { loading: false }],
}));
// The account holds every cluster permission somewhere.
vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({ loading: false, granted: new Set(["*"]), can: () => true }),
}));

const wrapper = ({ children }: { children: ReactNode }) => (
  <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
    {children}
  </NextIntlClientProvider>
);

describe("cluster settings offer what the cluster allows (#1867)", () => {
  it("takes a shared cluster's own answer over account-wide grants", () => {
    state.cluster = { slug: "shared", lifecycle: "managed", viewerPermissions: ["cluster.read"] };
    const { result } = renderHook(() => useClusterSettings("shared"), { wrapper });
    expect(result.current.access).toEqual({
      manage: false,
      update: false,
      users: false,
      unregister: false,
    });
  });

  it("offers what an owned cluster says the viewer holds", () => {
    state.cluster = {
      slug: "owned",
      lifecycle: "managed",
      viewerPermissions: ["cluster.read", "cluster.update", "cluster.unregister"],
    };
    const { result } = renderHook(() => useClusterSettings("owned"), { wrapper });
    expect(result.current.access).toEqual({
      manage: false,
      update: true,
      users: false,
      unregister: true,
    });
  });
});
