import { renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { AppPermissionsProvider, useAppCan } from "./app-permissions";

const state = vi.hoisted(() => ({ held: null as string[] | null }));

vi.mock("@apollo/client/react", () => ({
  useQuery: () => ({
    data: state.held ? { astroliftApp: { id: "a", viewerPermissions: state.held } } : undefined,
  }),
}));
// The account holds app.update and app.delete somewhere in the organization.
vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({
    loading: false,
    granted: new Set(["app.update", "app.delete"]),
    can: (p: string) => ["app.update", "app.delete"].includes(p),
  }),
}));

const framed = ({ children }: { children: ReactNode }) => (
  <AppPermissionsProvider slug="billing-api">{children}</AppPermissionsProvider>
);

describe("useAppCan (#1867)", () => {
  it("answers from the framing app once its permissions arrive", () => {
    state.held = ["app.read", "app.deploy"];
    const { result } = renderHook(
      () => [
        useAppCan("app.update"),
        useAppCan("app.deploy"),
        useAppCan("app.deploy", { optimistic: false }),
      ],
      { wrapper: framed }
    );
    expect(result.current).toEqual([false, true, true]);
  });

  it("bridges with account grants until the app answers, and outside a frame", () => {
    state.held = null;
    const inFrame = renderHook(() => [useAppCan("app.update"), useAppCan("app.deploy")], {
      wrapper: framed,
    });
    expect(inFrame.result.current).toEqual([true, false]);
    const outside = renderHook(() => useAppCan("app.delete"));
    expect(outside.result.current).toBe(true);
  });
});
