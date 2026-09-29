import { describe, expect, it, vi } from "vitest";

import { saveUiPrefs, setUiPrefsSaver } from "./ui-prefs-sync";

describe("the server saver", () => {
  it("does nothing with no saver registered (signed out: the change stays in this browser)", () => {
    expect(() => saveUiPrefs({ motion: "full" })).not.toThrow();
  });

  it("hands every change to the registered saver until it unregisters", () => {
    const save = vi.fn();
    const unregister = setUiPrefsSaver(save);
    saveUiPrefs({ motion: "full" });
    expect(save).toHaveBeenCalledExactlyOnceWith({ motion: "full" });
    unregister();
    saveUiPrefs({ motion: "reduced" });
    expect(save).toHaveBeenCalledTimes(1);
  });

  it("leaves a newer saver in place when an older one unregisters", () => {
    const older = vi.fn();
    const newer = vi.fn();
    const unregisterOlder = setUiPrefsSaver(older);
    const unregisterNewer = setUiPrefsSaver(newer);
    unregisterOlder();
    saveUiPrefs({ appView: "graph" });
    expect(newer).toHaveBeenCalledExactlyOnceWith({ appView: "graph" });
    expect(older).not.toHaveBeenCalled();
    unregisterNewer();
  });
});
