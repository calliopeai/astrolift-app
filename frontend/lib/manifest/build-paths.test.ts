import { describe, expect, it } from "vitest";

import { detectBuildPaths, manifestDirectory, manifestSetsBuildPaths } from "./build-paths";

const PLAIN = `name = "abbott-cgm"

[[workloads]]
name = "web"
kind = "deployment"

[[workloads.containers]]
name = "web"
is_primary = true
`;

describe("manifestDirectory (the cases backend test_build_context_159 pins)", () => {
  it.each([
    ["src/abbott-cgm/astrolift.toml", "src/abbott-cgm"],
    ["apps/web/astrolift.toml", "apps/web"],
    ["./services/api/astrolift.toml", "services/api"],
    ["services//api/astrolift.toml", "services/api"],
    ["astrolift.toml", "."],
    ["", "."],
    ["../outside/astrolift.toml", "."],
    ["src/../../outside/astrolift.toml", "."],
    ["/etc/astrolift.toml", "."],
  ])("%s builds from %s", (path, expected) => {
    expect(manifestDirectory(path)).toBe(expected);
  });
});

describe("manifestSetsBuildPaths", () => {
  it("is false for a manifest that names no build paths, or only the defaults", () => {
    expect(manifestSetsBuildPaths(PLAIN)).toBe(false);
    expect(
      manifestSetsBuildPaths(`${PLAIN}build_context = "."\ndockerfile_path = 'Dockerfile'\n`)
    ).toBe(false);
  });

  it.each([
    ['build_context = "../.."'],
    ["dockerfile_path = 'Dockerfile.web'"],
    ['  dockerfile = "Dockerfile.jobs"  # the nightly job'],
    ['containers = [{ name = "web", build_context = "sub" }]'],
  ])("is true for %s", (line) => {
    expect(manifestSetsBuildPaths(`${PLAIN}${line}\n`)).toBe(true);
  });

  it("ignores a commented-out line", () => {
    expect(manifestSetsBuildPaths(`${PLAIN}# build_context = "../.."\n`)).toBe(false);
  });
});

describe("detectBuildPaths", () => {
  it("builds a sub-directory manifest from its directory", () => {
    expect(detectBuildPaths("src/abbott-cgm/astrolift.toml", PLAIN)).toEqual({
      buildContext: "src/abbott-cgm",
      dockerfilePath: "Dockerfile",
      source: "manifestDirectory",
    });
  });

  it("keeps the repository root for a root manifest", () => {
    expect(detectBuildPaths("astrolift.toml", PLAIN)).toEqual({
      buildContext: ".",
      dockerfilePath: "Dockerfile",
      source: "repositoryRoot",
    });
  });

  it("keeps the repository root when the manifest sets its own paths", () => {
    const manifest = `${PLAIN}build_context = "../.."\n`;
    expect(detectBuildPaths("src/abbott-cgm/astrolift.toml", manifest).source).toBe(
      "manifestPaths"
    );
    expect(detectBuildPaths("astrolift.toml", manifest)).toEqual({
      buildContext: ".",
      dockerfilePath: "Dockerfile",
      source: "manifestPaths",
    });
  });
});
