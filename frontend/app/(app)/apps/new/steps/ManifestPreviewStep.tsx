"use client";

import type * as React from "react";

import { ManifestPreviewStepView } from "@/components/screens/apps/new/ManifestPreviewStep";
import { useManifestPreviewStep } from "@/components/screens/apps/new/use-manifest-preview-step";

import { hasMaskedEnvValues } from "../manifest-submit";
import type { WizardState } from "../wizard-client";

interface Props {
  state: WizardState;
  setState: React.Dispatch<React.SetStateAction<WizardState>>;
  setValid: (valid: boolean) => void;
}

export function ManifestPreviewStep({ state, setState, setValid }: Props) {
  const step = useManifestPreviewStep(state, setState, setValid);

  return (
    <ManifestPreviewStepView
      {...step}
      state={state}
      setState={setState}
      maskedEnvValues={hasMaskedEnvValues(state.manifestRaw)}
    />
  );
}
