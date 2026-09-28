"use client";

import type * as React from "react";

import { AgentProjectStepView } from "@/components/screens/agents/new/AgentProjectStep";
import { useAgentProjectStep } from "@/components/screens/agents/new/use-agent-project-step";

import type { WizardState } from "../wizard-client";

interface Props {
  state: WizardState;
  setState: React.Dispatch<React.SetStateAction<WizardState>>;
  setValid: (valid: boolean) => void;
}

/** Register-agent-repo step 3. The view owns the markup; the hook owns teams, projects, and validity. */
export function ProjectStep({ state, setState, setValid }: Props) {
  const project = useAgentProjectStep(state, setState, setValid);
  return <AgentProjectStepView {...project} state={state} setState={setState} />;
}
