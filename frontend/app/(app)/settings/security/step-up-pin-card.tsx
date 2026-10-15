"use client";

import { StepUpPinView } from "@/components/screens/settings/security/StepUpPin";
import { useStepUpPin } from "@/components/screens/settings/security/use-step-up-pin";

/** Step-up PIN card (#2399). Markup lives in StepUpPinView. */
export function StepUpPinCard() {
  return <StepUpPinView {...useStepUpPin()} />;
}
