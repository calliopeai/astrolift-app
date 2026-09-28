"use client";

import { WizardShell, type WizardShellProps } from "@/components/screens/apps/new/WizardShell";

export type { WizardShellProps, WizardStep } from "@/components/screens/apps/new/WizardShell";

/**
 * Wizard chrome for the register-agent-repo flow: the register-app wizard's
 * WizardShell with agent-appropriate defaults. Visited steps are clickable so
 * the operator can jump back.
 */
export function AgentWizardShell({
  title = "Register agent repo",
  description = "Connect a Git repository. We'll scan it for agent manifests and register each one as an agent workload.",
  ...rest
}: WizardShellProps) {
  return <WizardShell title={title} description={description} {...rest} />;
}
