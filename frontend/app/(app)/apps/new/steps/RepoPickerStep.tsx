"use client";

import type * as React from "react";

import { RepoPickerStepView } from "@/components/screens/apps/new/RepoPickerStep";
import { useRepoPicker } from "@/components/screens/apps/new/use-repo-picker";

import { isCiPushableKind } from "../ci-pushable";
import type { WizardState } from "../wizard-client";

interface Props {
  state: WizardState;
  setState: React.Dispatch<React.SetStateAction<WizardState>>;
  setValid: (valid: boolean) => void;
}

/** New app step 1 (Source), the repository part. The view lives in components/screens/apps/new; this wires the hook. */
export function RepoPickerStep({ state, setState, setValid }: Props) {
  return <RepoPickerStepView {...useRepoPicker({ state, setState, setValid, isCiPushableKind })} />;
}
