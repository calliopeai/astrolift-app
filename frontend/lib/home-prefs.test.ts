import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  applyServerHomePrefs,
  DEFAULT_HOME_PREFS,
  homePrefsFromServer,
  parseHomePrefs,
  STORAGE_KEY,
  useHomePrefs,
} from "./home-prefs";
import { setUiPrefsSaver, type ServerUiPrefs } from "./ui-prefs-sync";

const SERVER: ServerUiPrefs = {
  homeLayout: "operator",
  homeLayoutAsked: true,
  fleetView: "orbit",
  workflowView: "transit",
  appView: "auto",
  flowParticles: true,
  motion: "system",
  restrictedSettings: "show",
  restrictedSettingsChoice: null,
  restrictedSettingsOrgDefault: "show",
  appearance: {},
};

describe("parseHomePrefs", () => {
  it("reads unset, garbage and unknown layouts as the access default", () => {
    expect(parseHomePrefs(null)).toEqual({ layout: null, asked: false });
    expect(parseHomePrefs("{nope")).toEqual(DEFAULT_HOME_PREFS);
    expect(parseHomePrefs('{"layout":"finance"}')).toEqual(DEFAULT_HOME_PREFS);
  });

  it("keeps a layout the registry knows, which also means the question was answered", () => {
    expect(parseHomePrefs('{"layout":"operator"}')).toEqual({ layout: "operator", asked: true });
  });

  it("remembers a reset to the default as answered", () => {
    expect(parseHomePrefs('{"layout":null,"asked":true}')).toEqual({ layout: null, asked: true });
  });
});

describe("homePrefsFromServer", () => {
  it("takes the server's layout and answered flag", () => {
    expect(homePrefsFromServer(SERVER)).toEqual({ layout: "operator", asked: true });
    expect(homePrefsFromServer({ ...SERVER, homeLayout: null, homeLayoutAsked: false })).toEqual(
      DEFAULT_HOME_PREFS
    );
  });

  it("reads a layout this build does not know as the access default, keeping the answer", () => {
    expect(homePrefsFromServer({ ...SERVER, homeLayout: "finance" })).toEqual({
      layout: null,
      asked: true,
    });
  });
});

describe("the home store and the server", () => {
  afterEach(() => {
    window.localStorage.clear();
  });

  it("lets the server win over a stale browser copy", () => {
    window.localStorage.setItem(STORAGE_KEY, '{"layout":null,"asked":false}');
    const { result } = renderHook(() => useHomePrefs());
    act(() => applyServerHomePrefs(SERVER));
    expect(result.current[0]).toEqual({ layout: "operator", asked: true });
    expect(parseHomePrefs(window.localStorage.getItem(STORAGE_KEY))).toEqual({
      layout: "operator",
      asked: true,
    });
  });

  it("saves a choice and a reset as answered, and a server answer is not sent back", () => {
    const save = vi.fn();
    const unregister = setUiPrefsSaver(save);
    const { result } = renderHook(() => useHomePrefs());
    act(() => applyServerHomePrefs({ ...SERVER, homeLayout: null, homeLayoutAsked: false }));
    expect(save).not.toHaveBeenCalled();

    act(() => result.current[1]("operator"));
    expect(save).toHaveBeenLastCalledWith({ homeLayout: "operator", homeLayoutAsked: true });

    act(() => result.current[1](null));
    expect(result.current[0]).toEqual({ layout: null, asked: true });
    expect(save).toHaveBeenLastCalledWith({ homeLayout: null, homeLayoutAsked: true });
    unregister();
  });
});
