"use client";

import type * as React from "react";

import { AgentRepoPickerStepView } from "@/components/screens/agents/new/AgentRepoPickerStep";
import { useAgentRepoPicker } from "@/components/screens/agents/new/use-agent-repo-picker";

import type { WizardState } from "../wizard-client";

interface Props {
  state: WizardState;
  setState: React.Dispatch<React.SetStateAction<WizardState>>;
  setValid: (valid: boolean) => void;
}

/** Wizard step 1. The view lives in components/screens/agents/new; this wires the hook. */
export function RepoPickerStep({ state, setState, setValid }: Props) {
  return <AgentRepoPickerStepView {...useAgentRepoPicker({ state, setState, setValid })} />;
}
