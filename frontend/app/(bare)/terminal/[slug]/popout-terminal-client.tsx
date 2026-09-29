"use client";

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
}: {
  appSlug: string;
  podName: string;
  container: string;
  command?: string[];
}) {
  return (
    <PopoutTerminalScreen
      appSlug={appSlug}
      podName={podName}
      container={container}
      terminal={
        <TerminalEmulator
          appSlug={appSlug}
          podName={podName}
          container={container}
          command={command}
          standalone
          className="min-h-0 flex-1"
        />
      }
    />
  );
}
