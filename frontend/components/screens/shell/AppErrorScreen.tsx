"use client";

import { ErrorState } from "@calliopelabs/ui";
import { useTranslations } from "next-intl";

export interface AppErrorScreenProps {
  /** The thrown error's message; empty falls back to a generic line. */
  message: string;
  /** Next's server-error digest, shown so a report can be traced. */
  digest?: string;
  onReset: () => void;
}

/**
 * The (app) group's error boundary. A deterministic error (bad param,
 * persistent GraphQL failure) makes reset() loop on the same failing subtree,
 * so the dashboard link is always offered as an escape.
 */
export function AppErrorScreen({ message, digest, onReset }: AppErrorScreenProps) {
  const t = useTranslations("errorScreens");
  return (
    <ErrorState
      variant="page"
      title={t("title")}
      error={message || t("unexpected")}
      digest={digest}
      onRetry={onReset}
      retryLabel={t("tryAgain")}
      homeHref="/dashboard"
      homeLabel={t("goToDashboard")}
    />
  );
}
