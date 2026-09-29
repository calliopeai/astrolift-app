"use client";

import type * as React from "react";

import { AppDetailsStepView } from "@/components/screens/apps/new/AppDetailsStep";
import { useAppDetailsStep } from "@/components/screens/apps/new/use-app-details-step";

import type { WizardState } from "../wizard-client";

interface Props {
  state: WizardState;
  setState: React.Dispatch<React.SetStateAction<WizardState>>;
  setValid: (valid: boolean) => void;
}

/** New app step 2 (Run), the details part. The view owns the markup; the hook owns teams, projects, and validity. */
export function AppDetailsStep({ state, setState, setValid }: Props) {
  const details = useAppDetailsStep(state, setState, setValid);
  return <AppDetailsStepView {...details} state={state} setState={setState} />;
}
