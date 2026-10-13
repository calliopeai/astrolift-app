"use client";

import { NextIntlClientProvider } from "next-intl";
import { useEffect } from "react";
import { captureException } from "@/lib/sentry";
import { maybeReloadOnChunkError } from "@/lib/chunk-reload";
import { GlobalErrorScreen } from "@/components/screens/shell/GlobalErrorScreen";
import {
  GLOBAL_ERROR_MESSAGES,
  useCookieLocale,
} from "@/components/screens/shell/global-error-messages";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  // The root layout, and the intl provider in it, are gone: give the screen
  // its own, with only its copy, in the language the viewer chose.
  const locale = useCookieLocale();
  useEffect(() => {
    if (maybeReloadOnChunkError(error)) return;
    captureException(error);
  }, [error]);

  return (
    <html lang={locale}>
      <body>
        <NextIntlClientProvider
          locale={locale}
          messages={GLOBAL_ERROR_MESSAGES[locale]}
          timeZone="UTC"
        >
          <GlobalErrorScreen message={error.message} digest={error.digest} onReset={reset} />
        </NextIntlClientProvider>
      </body>
    </html>
  );
}
