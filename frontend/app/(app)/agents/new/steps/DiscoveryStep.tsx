"use client";

import type * as React from "react";

import { AgentDiscoveryStepView } from "@/components/screens/agents/new/AgentDiscoveryStep";
import { useAgentDiscoveryStep } from "@/components/screens/agents/new/use-agent-discovery-step";

import type { WizardState } from "@/components/screens/agents/new/use-new-agent";

interface Props {
  state: WizardState;
  setState: React.Dispatch<React.SetStateAction<WizardState>>;
  setValid: (valid: boolean) => void;
}

/** New agent, Source: the agents a scan finds. The view owns the markup; the hook owns the scan and validity. */
export function DiscoveryStep({ state, setState, setValid }: Props) {
  const discovery = useAgentDiscoveryStep(state, setState, setValid);
  return <AgentDiscoveryStepView {...discovery} state={state} />;
}
