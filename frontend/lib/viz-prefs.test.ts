import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { setUiPrefsSaver, type ServerUiPrefs } from "./ui-prefs-sync";
import {
  applyServerVizPrefs,
  DEFAULT_VIZ_PREFS,
  parseVizPrefs,
  resolveMotion,
  STORAGE_KEY,
  useVizPrefs,
  vizPrefsFromServer,
} from "./viz-prefs";

const SERVER: ServerUiPrefs = {
  homeLayout: null,
  homeLayoutAsked: false,
  fleetView: "graph",
  workflowView: "isometric",
  appView: "classic",
  flowParticles: false,
  motion: "reduced",
  restrictedSettings: "show",
  restrictedSettingsChoice: null,
  restrictedSettingsOrgDefault: "show",
  appearance: {},
};

describe("parseVizPrefs", () => {
  it("falls back to defaults for missing or broken storage", () => {
    expect(parseVizPrefs(null)).toEqual(DEFAULT_VIZ_PREFS);
    expect(parseVizPrefs("{nope")).toEqual(DEFAULT_VIZ_PREFS);
    expect(parseVizPrefs("42")).toEqual(DEFAULT_VIZ_PREFS);
  });

  it("keeps valid fields and drops unknown ones field by field", () => {
    const parsed = parseVizPrefs(
      JSON.stringify({ fleetView: "hive", workflowView: "gone", flowParticles: false })
    );
    expect(parsed).toEqual({ ...DEFAULT_VIZ_PREFS, fleetView: "hive", flowParticles: false });
  });
});

describe("vizPrefsFromServer", () => {
  it("takes every field the server resolved", () => {
    expect(vizPrefsFromServer(SERVER)).toEqual({
      fleetView: "graph",
      workflowView: "isometric",
      appView: "classic",
      flowParticles: false,
      motion: "reduced",
    });
  });

  it("reads a value this build does not know as the product default", () => {
    expect(vizPrefsFromServer({ ...SERVER, fleetView: "retired", motion: "sideways" })).toEqual({
      ...vizPrefsFromServer(SERVER),
      fleetView: DEFAULT_VIZ_PREFS.fleetView,
      motion: DEFAULT_VIZ_PREFS.motion,
    });
  });
});

describe("the viz store and the server", () => {
  afterEach(() => {
    window.localStorage.clear();
  });

  it("lets the server win over a stale browser copy, and keeps the browser copy in step", () => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ fleetView: "hive", motion: "full" }));
    const { result } = renderHook(() => useVizPrefs());
    act(() => applyServerVizPrefs(SERVER));
    expect(result.current[0]).toEqual(vizPrefsFromServer(SERVER));
    expect(parseVizPrefs(window.localStorage.getItem(STORAGE_KEY))).toEqual(
      vizPrefsFromServer(SERVER)
    );
  });

  it("sends a change to the server as just the changed fields, and a server answer is not sent back", () => {
    const save = vi.fn();
    const unregister = setUiPrefsSaver(save);
    const { result } = renderHook(() => useVizPrefs());
    act(() => applyServerVizPrefs(SERVER));
    expect(save).not.toHaveBeenCalled();
    act(() => result.current[1]({ fleetView: "swarm" }));
    expect(save).toHaveBeenCalledExactlyOnceWith({ fleetView: "swarm" });
    expect(result.current[0]).toEqual({ ...vizPrefsFromServer(SERVER), fleetView: "swarm" });
    unregister();
  });
});

describe("resolveMotion", () => {
  it("follows the OS only when set to system", () => {
    expect(resolveMotion("system", true)).toBe("reduced");
    expect(resolveMotion("system", false)).toBe("full");
    expect(resolveMotion("full", true)).toBe("full");
    expect(resolveMotion("reduced", false)).toBe("reduced");
  });
});
