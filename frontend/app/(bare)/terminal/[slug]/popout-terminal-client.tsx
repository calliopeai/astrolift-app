"use client";

import { isReviewedExecTarget } from "@/lib/exec-session";

import { TerminalEmulator } from "@/components/observability";
import { PopoutTerminalScreen } from "@/components/screens/shell/PopoutTerminalScreen";

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
  target,
}: {
  appSlug: string;
  podName: string;
  container: string;
  command?: string[];
  target?: unknown;
}) {
  return (
    <PopoutTerminalScreen
      appSlug={appSlug}
      podName={podName}
      container={container}
      terminal={
        <TerminalEmulator
          appSlug={appSlug}
          podName={target !== undefined && !isReviewedExecTarget(target) ? "" : podName}
          container={container}
          command={command}
          target={isReviewedExecTarget(target) ? target : undefined}
          standalone
          className="min-h-0 flex-1"
        />
      }
    />
  );
}
