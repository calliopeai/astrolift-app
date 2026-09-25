import { describe, expect, it } from "vitest";

import { hasMaskedEnvValues, manifestRawForSubmit } from "./manifest-submit";

const MASKED_VALUE = "[ASTROLIFT_REDACTED_ENV_VALUE]";

const MASKED_MANIFEST = `name = "hello"

[[workloads]]
name = "web"

  [[workloads.containers]]
  name = "app"

    [workloads.containers.env]
    API_KEY = "${MASKED_VALUE}"
`;

describe("manifestRawForSubmit", () => {
  it("sends null for a repo manifest the server masked, so registration reads the repo itself", () => {
    expect(
      manifestRawForSubmit({
        manifestLater: false,
        manifestFromRepo: true,
        manifestRaw: MASKED_MANIFEST,
      })
    ).toBeNull();
  });

  it("sends an edited masked manifest as typed, for the server to refuse the placeholder", () => {
    expect(
      manifestRawForSubmit({
        manifestLater: false,
        manifestFromRepo: false,
        manifestRaw: MASKED_MANIFEST,
      })
    ).toBe(MASKED_MANIFEST.trim());
  });

  it("sends an unmasked repo manifest inline", () => {
    const raw = MASKED_MANIFEST.replace(MASKED_VALUE, "sk-live-real");
    expect(
      manifestRawForSubmit({ manifestLater: false, manifestFromRepo: true, manifestRaw: raw })
    ).toBe(raw.trim());
  });

  it("sends null for 'set up manifest later' and for an empty manifest", () => {
    expect(
      manifestRawForSubmit({ manifestLater: true, manifestFromRepo: false, manifestRaw: "x" })
    ).toBeNull();
    expect(
      manifestRawForSubmit({ manifestLater: false, manifestFromRepo: false, manifestRaw: "  " })
    ).toBeNull();
  });
});

describe("hasMaskedEnvValues", () => {
  it("spots a masked value, a masked comment and a masked document", () => {
    expect(hasMaskedEnvValues(MASKED_MANIFEST)).toBe(true);
    expect(hasMaskedEnvValues("# [ASTROLIFT_REDACTED_ENV_COMMENT:1]\n")).toBe(true);
    expect(hasMaskedEnvValues("# [ASTROLIFT_REDACTED_UNPARSEABLE_MANIFEST]\n")).toBe(true);
    expect(hasMaskedEnvValues('name = "hello"\n')).toBe(false);
  });
});
