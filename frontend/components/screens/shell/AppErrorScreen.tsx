"use client";

import { AlertCircleIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";

import { Button } from "@/components/ui/button";

export interface AppErrorScreenProps {
  /** The thrown error's message; empty falls back to a generic line. */
  message: string;
  /** Next's server-error digest, shown so a report can be traced. */
  digest?: string;
  onReset: () => void;
}

/** The (app) group's error boundary. */
export function AppErrorScreen({ message, digest, onReset }: AppErrorScreenProps) {
  const t = useTranslations("errorScreens");
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-4 p-6 text-center">
      <div className="bg-destructive/10 text-destructive rounded-full p-4">
        <AlertCircleIcon className="h-8 w-8" />
      </div>
      <div>
        <h2 className="text-lg font-semibold">{t("title")}</h2>
        <p className="text-muted-foreground mt-1 max-w-sm text-sm">{message || t("unexpected")}</p>
        {digest && (
          <p className="text-muted-foreground mt-2 font-mono text-xs">{t("errorId", { digest })}</p>
        )}
      </div>
      {/* A deterministic error (bad param, persistent GraphQL failure) makes
          reset() loop on the same failing subtree — always offer an escape to
          a known-good page. */}
      <div className="flex items-center gap-2">
        <Button onClick={onReset} variant="outline" size="sm">
          {t("tryAgain")}
        </Button>
        <Button asChild variant="ghost" size="sm">
          <Link href="/dashboard">{t("goToDashboard")}</Link>
        </Button>
      </div>
    </div>
  );
}
