"use client";

import type * as React from "react";

import {
  ReviewSubmitStepView,
  type SideEffectStep,
} from "@/components/screens/apps/new/ReviewSubmitStep";

import { isCiPushableKind } from "../ci-pushable";
import type { WizardState } from "../wizard-client";

export type { SideEffectStep, StepStatus } from "@/components/screens/apps/new/ReviewSubmitStep";

interface Props {
  state: WizardState;
  setState: React.Dispatch<React.SetStateAction<WizardState>>;
  onJumpToStep: (step: 1 | 2) => void;
  submitting: boolean;
  submitError: string | null;
  sideEffects: SideEffectStep[];
}

/** New app step 3, Review. The view lives in components/screens/apps/new; this wires wizard state. */
export function ReviewSubmitStep({ state, setState, ...rest }: Props) {
  return (
    <ReviewSubmitStepView
      {...rest}
      state={state}
      canPushCiWorkflow={isCiPushableKind(state.connectionKind)}
      onPushCiWorkflowChange={(checked) => setState((s) => ({ ...s, pushCiWorkflow: checked }))}
      onTriggerFirstDeployChange={(checked) =>
        setState((s) => ({ ...s, triggerFirstDeploy: checked }))
      }
    />
  );
}
