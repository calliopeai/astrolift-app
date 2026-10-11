import { renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  app: null as { viewerPermissions?: string[] } | null,
  granted: new Set<string>(),
  loading: false,
}));
vi.mock("./use-app-settings", () => ({
  useAppSettings: () => ({ app: state.app, loading: false }),
}));
vi.mock("@/components/settings/use-settings-section", () => ({
  useSettingsSection: () => null,
}));
vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({
    loading: state.loading,
    granted: state.granted,
    can: (p: string) => state.granted.has(p),
  }),
}));

import { useAppSettingsTab } from "./use-app-settings-tab";

describe("app settings access is scoped to the app (#1867)", () => {
  beforeEach(() => {
    state.app = null;
    state.granted = new Set(["app.update", "app.deploy", "app.delete", "webhook.update"]);
    state.loading = false;
  });

  it("an account-wide grant held elsewhere does not offer an action this app refuses", () => {
    state.app = { viewerPermissions: ["app.read", "app.update"] };
    const { result } = renderHook(() => useAppSettingsTab("checkout"));
    expect(result.current.access).toEqual({
      update: true,
      deploy: false,
      delete: false,
      webhooks: false,
    });
  });

  it("account grants bridge the first load until the app's own permissions arrive", () => {
    const { result } = renderHook(() => useAppSettingsTab("checkout"));
    expect(result.current.access.delete).toBe(true);
  });

  it("an app the viewer holds nothing on offers nothing", () => {
    state.app = { viewerPermissions: [] };
    const { result } = renderHook(() => useAppSettingsTab("checkout"));
    expect(Object.values(result.current.access).every((v) => v === false)).toBe(true);
  });
});
