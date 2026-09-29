"use client";

import type * as React from "react";

import { AgentRepoPickerStepView } from "@/components/screens/agents/new/AgentRepoPickerStep";
import { useAgentRepoPicker } from "@/components/screens/agents/new/use-agent-repo-picker";

import type { WizardState } from "@/components/screens/agents/new/use-new-agent";

interface Props {
  state: WizardState;
  setState: React.Dispatch<React.SetStateAction<WizardState>>;
  setValid: (valid: boolean) => void;
}

/** New agent, Source: the repository. The view lives in components/screens/agents/new; this wires the hook. */
export function RepoPickerStep({ state, setState, setValid }: Props) {
  return <AgentRepoPickerStepView {...useAgentRepoPicker({ state, setState, setValid })} />;
}
