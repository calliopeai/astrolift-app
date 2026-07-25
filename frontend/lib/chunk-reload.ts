/**
 * Auto-recover from a post-deploy chunk-load failure.
 *
 * A frontend deploy rotates every chunk hash, so a tab that was open across the
 * deploy references assets the new build no longer serves. Next surfaces that as
 * a ChunkLoadError ("Loading chunk N failed" / "Failed to fetch dynamically
 * imported module"). It is transient: a reload fetches the fresh HTML + current
 * chunks. Rather than showing "Something went wrong" on every deploy, an error
 * boundary can call this to reload once.
 *
 * Rate-limited via sessionStorage so a genuinely-missing chunk (a real 404, not
 * a stale tab) can't reload-loop — after one recent reload it returns false and
 * the caller falls through to the normal error UI (the escape hatch).
 *
 * Returns true when it initiated a reload; the caller should `return` early
 * (skip logging/reporting a transient error we're already recovering from).
 */
export function maybeReloadOnChunkError(error: { name?: string; message?: string } | null | undefined): boolean {
  if (!error || typeof window === "undefined") return false;
  const isChunkError =
    error.name === "ChunkLoadError" ||
    /Loading chunk [\w-]+ failed|Failed to fetch dynamically imported module|error loading dynamically imported module|importing a module script failed/i.test(
      error.message || ""
    );
  if (!isChunkError) return false;
  try {
    const KEY = "astrolift.chunkReloadAt";
    const last = Number(window.sessionStorage.getItem(KEY) || "0");
    if (Date.now() - last > 10_000) {
      window.sessionStorage.setItem(KEY, String(Date.now()));
      window.location.reload();
      return true;
    }
  } catch {
    // sessionStorage blocked (private mode / partitioned) — fall through to the
    // normal error UI rather than risk an unguarded reload loop.
  }
  return false;
}
