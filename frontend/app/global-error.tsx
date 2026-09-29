"use client";

import { useEffect } from "react";
import { captureException } from "@/lib/sentry";
import { maybeReloadOnChunkError } from "@/lib/chunk-reload";
import { GlobalErrorScreen } from "@/components/screens/shell/GlobalErrorScreen";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    if (maybeReloadOnChunkError(error)) return;
    captureException(error);
  }, [error]);

  return (
    <html>
      <body>
        <GlobalErrorScreen message={error.message} digest={error.digest} onReset={reset} />
      </body>
    </html>
  );
}
