import { PopoutTerminalClient } from "./popout-terminal-client";

export const metadata = {
  title: "Shell · Astrolift",
};

/**
 * The popped-out pod shell (#1246) — `window.open` target for the Shell tab's
 * pop-out button, and a legitimate direct URL in its own right.
 *
 * A separate window is a separate React tree, so this necessarily opens a
 * *new* exec session rather than adopting the one on the originating tab.
 * The backend's ring buffer makes that survivable: the terminal replays
 * recent output on connect, so the window opens warm.
 */
export default async function PopoutTerminalPage({
  params,
  searchParams,
}: {
  params: Promise<{ slug: string }>;
  searchParams: Promise<{ pod?: string; container?: string; command?: string }>;
}) {
  const { slug } = await params;
  const { pod, container, command } = await searchParams;
  return (
    <PopoutTerminalClient
      appSlug={slug}
      podName={pod ?? ""}
      container={container ?? ""}
      command={command ? command.split(" ").filter(Boolean) : undefined}
    />
  );
}
