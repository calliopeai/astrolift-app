"use client";

import { useEffect } from "react";
import { captureException } from "@/lib/sentry";
import { maybeReloadOnChunkError } from "@/lib/chunk-reload";
import { RootErrorScreen } from "@/components/screens/shell/RootErrorScreen";

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    if (maybeReloadOnChunkError(error)) return;
    console.error(error);
    captureException(error);
  }, [error]);

  return <RootErrorScreen message={error.message} digest={error.digest} onReset={reset} />;
}
