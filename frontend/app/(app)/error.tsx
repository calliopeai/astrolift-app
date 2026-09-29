"use client";

import { useEffect } from "react";
import { captureException } from "@/lib/sentry";
import { maybeReloadOnChunkError } from "@/lib/chunk-reload";
import { AppErrorScreen } from "@/components/screens/shell/AppErrorScreen";

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Recover transparently from a post-deploy stale-chunk failure; only report
    // the error if we're NOT already reloading to fix it.
    if (maybeReloadOnChunkError(error)) return;
    console.error(error);
    captureException(error);
  }, [error]);

  return <AppErrorScreen message={error.message} digest={error.digest} onReset={reset} />;
}
