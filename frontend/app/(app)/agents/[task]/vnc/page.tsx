import { AgentVncPopout } from "./popout-client";

export const metadata = { title: "Live agent session · Astrolift" };

/**
 * Pop-out route — a dedicated, full-height noVNC session for a single
 * agent task, opened in a new tab from the theatre gallery's "Pop out"
 * affordance. Auth is enforced by the (app) layout's token gate; the
 * VNC relay re-checks the session cookie + ``agent_task.watch`` itself.
 */
export default async function AgentVncPopoutPage({
  params,
}: {
  params: Promise<{ task: string }>;
}) {
  const { task } = await params;
  return <AgentVncPopout taskId={task} />;
}
