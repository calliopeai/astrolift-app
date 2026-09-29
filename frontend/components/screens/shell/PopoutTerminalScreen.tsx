"use client";

import { useTranslations } from "next-intl";
import type * as React from "react";

export interface PopoutTerminalScreenProps {
  appSlug: string;
  podName: string;
  container: string;
  /** The live terminal; rendered only once a pod and container are picked. */
  terminal: React.ReactNode;
}

/**
 * Fills the popped-out window with the terminal and a one-line target
 * caption, or asks for a target when the URL names none.
 */
export function PopoutTerminalScreen({
  appSlug,
  podName,
  container,
  terminal,
}: PopoutTerminalScreenProps) {
  const t = useTranslations("apps.shell.terminal");

  if (!podName || !container) {
    return (
      <div className="text-muted-foreground flex h-full items-center justify-center p-6 text-sm">
        {t("pickTarget")}
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col gap-2 p-3">
      <div className="text-muted-foreground shrink-0 font-mono text-xs">
        {appSlug} · {podName} · {container}
      </div>
      {terminal}
    </div>
  );
}
