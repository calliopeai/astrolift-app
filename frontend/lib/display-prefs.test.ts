import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  applyServerDisplayPrefs,
  DEFAULT_DISPLAY_PREFS,
  displayPrefsFromServer,
  parseDisplayPrefs,
  resolveRestrictedSettings,
  STORAGE_KEY,
  useDisplayPrefs,
} from "./display-prefs";
import { setUiPrefsSaver, type ServerUiPrefs } from "./ui-prefs-sync";

const SERVER: ServerUiPrefs = {
  homeLayout: null,
  homeLayoutAsked: false,
  fleetView: "orbit",
  workflowView: "transit",
  appView: "auto",
  flowParticles: true,
  motion: "system",
  restrictedSettings: "hide",
  restrictedSettingsChoice: null,
  restrictedSettingsOrgDefault: "hide",
  appearance: {},
};

describe("resolveRestrictedSettings", () => {
  it("takes the person's choice over the org default", () => {
    expect(resolveRestrictedSettings("show", "hide")).toBe("show");
    expect(resolveRestrictedSettings("hide", "show")).toBe("hide");
  });

  it("falls back to the org default when the person has not chosen, then to the product default", () => {
    expect(resolveRestrictedSettings(null, "hide")).toBe("hide");
    expect(resolveRestrictedSettings(null, null)).toBe("show");
  });
});

describe("parseDisplayPrefs", () => {
  it("defaults to showing restricted settings read-only, with no choice and no org default", () => {
    expect(parseDisplayPrefs(null)).toEqual({
      restrictedSettings: "show",
      restrictedSettingsChoice: null,
      restrictedSettingsOrgDefault: null,
    });
    expect(parseDisplayPrefs("{nope")).toEqual(DEFAULT_DISPLAY_PREFS);
    expect(parseDisplayPrefs("null")).toEqual(DEFAULT_DISPLAY_PREFS);
    expect(parseDisplayPrefs('{"restrictedSettings":"sideways"}')).toEqual(DEFAULT_DISPLAY_PREFS);
  });

  it("reads a value stored before #2154 as the person's choice", () => {
    expect(parseDisplayPrefs('{"restrictedSettings":"hide"}')).toEqual({
      restrictedSettings: "hide",
      restrictedSettingsChoice: "hide",
      restrictedSettingsOrgDefault: null,
    });
  });

  it("keeps a cached org default under no choice, and does not mistake the resolved value for a choice", () => {
    const stored = JSON.stringify({
      restrictedSettings: "hide",
      restrictedSettingsChoice: null,
      restrictedSettingsOrgDefault: "hide",
    });
    expect(parseDisplayPrefs(stored)).toEqual({
      restrictedSettings: "hide",
      restrictedSettingsChoice: null,
      restrictedSettingsOrgDefault: "hide",
    });
  });
});

describe("displayPrefsFromServer", () => {
  it("applies the org default when the person has not chosen", () => {
    expect(displayPrefsFromServer(SERVER).restrictedSettings).toBe("hide");
  });

  it("applies the person's choice over the org default", () => {
    expect(
      displayPrefsFromServer({ ...SERVER, restrictedSettingsChoice: "show" }).restrictedSettings
    ).toBe("show");
  });

  it("reads an unknown value as unset, falling through to the next rule", () => {
    expect(
      displayPrefsFromServer({
        ...SERVER,
        restrictedSettingsChoice: "sideways",
        restrictedSettingsOrgDefault: "hide",
      })
    ).toEqual({
      restrictedSettings: "hide",
      restrictedSettingsChoice: null,
      restrictedSettingsOrgDefault: "hide",
    });
    expect(
      displayPrefsFromServer({
        ...SERVER,
        restrictedSettingsChoice: null,
        restrictedSettingsOrgDefault: "sideways",
      }).restrictedSettings
    ).toBe("show");
  });
});

describe("the display store and the server", () => {
  afterEach(() => {
    window.localStorage.clear();
  });

  it("lets the server win over a stale browser choice", () => {
    window.localStorage.setItem(STORAGE_KEY, '{"restrictedSettings":"show"}');
    const { result } = renderHook(() => useDisplayPrefs());
    act(() => applyServerDisplayPrefs(SERVER));
    expect(result.current[0]).toEqual({
      restrictedSettings: "hide",
      restrictedSettingsChoice: null,
      restrictedSettingsOrgDefault: "hide",
    });
    expect(parseDisplayPrefs(window.localStorage.getItem(STORAGE_KEY))).toEqual(result.current[0]);
  });

  it("saves a choice, and clearing it goes back to the org default and resets it on the server", () => {
    const save = vi.fn();
    const unregister = setUiPrefsSaver(save);
    const { result } = renderHook(() => useDisplayPrefs());
    act(() => applyServerDisplayPrefs(SERVER));
    expect(save).not.toHaveBeenCalled();

    act(() => result.current[1]("show"));
    expect(result.current[0].restrictedSettings).toBe("show");
    expect(save).toHaveBeenLastCalledWith({ restrictedSettings: "show" });

    act(() => result.current[1](null));
    expect(result.current[0]).toEqual({
      restrictedSettings: "hide",
      restrictedSettingsChoice: null,
      restrictedSettingsOrgDefault: "hide",
    });
    expect(save).toHaveBeenLastCalledWith({ restrictedSettings: null });
    unregister();
  });
});
