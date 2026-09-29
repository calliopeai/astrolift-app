"use client";

import type * as React from "react";

import { AgentProjectStepView } from "@/components/screens/agents/new/AgentProjectStep";
import { useAgentProjectStep } from "@/components/screens/agents/new/use-agent-project-step";

import type { WizardState } from "@/components/screens/agents/new/use-new-agent";

interface Props {
  state: WizardState;
  setState: React.Dispatch<React.SetStateAction<WizardState>>;
  setValid: (valid: boolean) => void;
}

/** New agent, Configure: the project. The view owns the markup; the hook owns teams, projects, and validity. */
export function ProjectStep({ state, setState, setValid }: Props) {
  const project = useAgentProjectStep(state, setState, setValid);
  return <AgentProjectStepView {...project} state={state} setState={setState} />;
}
