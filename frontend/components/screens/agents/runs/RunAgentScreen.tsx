import type * as React from "react";

import { ShellHeader } from "@/components/shell/ShellHeader";
import { areaSwitcher, NAV } from "@/lib/shell/nav-model";

export interface RunAgentScreenProps {
  /** The dispatch form (DispatchTabView with its data). */
  children: React.ReactNode;
}

/**
 * Agents › Runs › Run agent (spec 44 §5.4): one run of any registered agent
 * with inputs, an environment override and a timeout. More than three fields,
 * so a page rather than a sheet; the plain Run now sits on each agent's title
 * row and row menu. This is the old fleet page's Dispatch tab. Pure chrome.
 */
export function RunAgentScreen({ children }: RunAgentScreenProps) {
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-6">
      <ShellHeader
        crumbs={[
          areaSwitcher(NAV, "agents", "runs"),
          { label: "Runs", href: "/tasks" },
          { label: "Run agent" },
        ]}
        title="Run agent"
        context="A single run now, with inputs. Schedules, loops and triggers are set on the agent."
      />
      <div className="max-w-5xl min-w-0">{children}</div>
    </div>
  );
}
