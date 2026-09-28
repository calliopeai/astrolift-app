"use client";

import type * as React from "react";

import { AppDeployStrategyStep } from "@/components/screens/apps/new/AppDeployStrategyStep";
import { useAppDeployStrategyStep } from "@/components/screens/apps/new/use-app-deploy-strategy-step";

import { ApproverSelector } from "../components/ApproverSelector";
import type { WizardState } from "../wizard-client";

interface Props {
  state: WizardState;
  setState: React.Dispatch<React.SetStateAction<WizardState>>;
  setValid: (valid: boolean) => void;
}

export function DeployStrategyStep({ state, setState, setValid }: Props) {
  const { orgSlug, setApproverPickerValid } = useAppDeployStrategyStep(state, setValid);

  return (
    <AppDeployStrategyStep
      state={state}
      setState={setState}
      approverSlot={
        <ApproverSelector
          orgSlug={orgSlug}
          value={{
            approverUserIds: state.approverUserIds,
            approverTeamId: state.approverTeamId,
            minimumApprovals: state.minimumApprovals,
          }}
          onChange={(next) =>
            setState((s) => ({
              ...s,
              approverUserIds: next.approverUserIds,
              approverTeamId: next.approverTeamId,
              minimumApprovals: next.minimumApprovals,
            }))
          }
          onValidityChange={setApproverPickerValid}
        />
      }
    />
  );
}
