"use client";

import { useTranslations } from "next-intl";

import { TerminalEmulator } from "@/components/observability";

/**
 * Fills the popped-out window with the terminal and a one-line target
 * caption. `standalone` drops the resize handle, the expand toggle and the
 * pop-out button — the OS window already does all three.
 */
export function PopoutTerminalClient({
  appSlug,
  podName,
  container,
  command,
}: {
  appSlug: string;
  podName: string;
  container: string;
  command?: string[];
}) {
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
      <TerminalEmulator
        appSlug={appSlug}
        podName={podName}
        container={container}
        command={command}
        standalone
        className="min-h-0 flex-1"
      />
    </div>
  );
}
