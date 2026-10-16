/**
 * Client-side mirror of the backend ``astrolift_manifest.build_context``
 * (astrolift-cli #159): the build context a new registration starts with.
 *
 * A manifest in a sub-directory builds from that directory
 * (``src/abbott-cgm/astrolift.toml`` builds ``src/abbott-cgm``), with the
 * ``Dockerfile`` resolved inside it. A manifest that sets ``build_context`` or
 * ``dockerfile_path`` itself keeps the repository root as the app's context,
 * so its own paths apply. Regex-grade, like the wizard's other manifest
 * checks: this only decides what the wizard shows. An untouched field is sent
 * as null and the server detects the context again, authoritatively.
 */

export const DEFAULT_BUILD_CONTEXT = ".";
export const DEFAULT_DOCKERFILE = "Dockerfile";

/** Why the detected context is what it is. */
export type BuildContextSource = "manifestDirectory" | "repositoryRoot" | "manifestPaths";

export interface DetectedBuildPaths {
  buildContext: string;
  dockerfilePath: string;
  source: BuildContextSource;
}

/**
 * The manifest's repo-relative directory, normalized; ``"."`` at the root,
 * and for a path that is absolute or climbs above the repository.
 */
export function manifestDirectory(manifestPath: string): string {
  const path = manifestPath.trim();
  if (path.startsWith("/")) return DEFAULT_BUILD_CONTEXT;
  const parts: string[] = [];
  for (const segment of path.split("/").slice(0, -1)) {
    if (segment === "" || segment === ".") continue;
    if (segment === "..") {
      if (parts.length === 0) return DEFAULT_BUILD_CONTEXT;
      parts.pop();
      continue;
    }
    parts.push(segment);
  }
  return parts.length ? parts.join("/") : DEFAULT_BUILD_CONTEXT;
}

const BUILD_PATH_KEY =
  /\b(build_context|dockerfile_path|dockerfile)\s*=\s*(?:"([^"]*)"|'([^']*)')/g;

/**
 * Whether a container, job or task in the manifest sets a ``build_context``
 * or Dockerfile of its own. The defaults (``"."``, ``"Dockerfile"``) are not
 * its own; commented-out lines do not count.
 */
export function manifestSetsBuildPaths(manifestRaw: string): boolean {
  for (const line of manifestRaw.split("\n")) {
    if (line.trimStart().startsWith("#")) continue;
    for (const match of line.matchAll(BUILD_PATH_KEY)) {
      const value = match[2] ?? match[3] ?? "";
      const fallback = match[1] === "build_context" ? DEFAULT_BUILD_CONTEXT : DEFAULT_DOCKERFILE;
      if (value !== "" && value !== fallback) return true;
    }
  }
  return false;
}

/** What a new registration of this manifest builds from, before any edit. */
export function detectBuildPaths(manifestPath: string, manifestRaw: string): DetectedBuildPaths {
  const root = { buildContext: DEFAULT_BUILD_CONTEXT, dockerfilePath: DEFAULT_DOCKERFILE };
  if (manifestSetsBuildPaths(manifestRaw)) return { ...root, source: "manifestPaths" };
  const directory = manifestDirectory(manifestPath);
  if (directory === DEFAULT_BUILD_CONTEXT) return { ...root, source: "repositoryRoot" };
  return {
    buildContext: directory,
    dockerfilePath: DEFAULT_DOCKERFILE,
    source: "manifestDirectory",
  };
}
