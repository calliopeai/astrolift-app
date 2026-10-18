"use client";

import { BrainSubstrateForm, type SubstrateServerError } from "../BrainSubstrateForm";
import type { CellMatrix } from "../substrate-cells";
import { BrainCreationProgress, type BrainCreationProgressProps } from "./BrainCreationProgress";
import { BrainDetailsStep } from "./BrainDetailsStep";
import { BrainReviewStep } from "./BrainReviewStep";
import { BrainSourceStep, type WizardConnection } from "./BrainSourceStep";
import type {
  DraftField,
  NewBrainDraft,
  NewBrainStep,
  Problem,
  WizardProject,
} from "./new-brain-model";
import { NewBrainScreen } from "./NewBrainScreen";

export interface NewBrainWizardProps {
  /** Set once a request was accepted: the creation's progress replaces the form. */
  progress: BrainCreationProgressProps | null;
  denied: boolean;
  step: NewBrainStep;
  onStep: (step: NewBrainStep) => void;
  onBack?: () => void;
  onContinue: () => void;
  continueDisabled: boolean;
  busy: boolean;
  onCancel: () => void;
  draft: NewBrainDraft;
  onChange: (next: NewBrainDraft) => void;
  problems: Partial<Record<DraftField, Problem>>;
  serverErrors: Partial<Record<DraftField, string>>;
  substrateError: SubstrateServerError | null;
  /** Mimwell's matrix for the substrate step; null until it arrives. */
  matrix: CellMatrix | null;
  /** The ref the install pins, once read, when the draft names none. */
  pinnedRef: string | null;
  generalError: string | null;
  connections: WizardConnection[];
  connectionsLoading: boolean;
  connectionsError: string | null;
  onRetryConnections: () => void;
  projects: WizardProject[];
  projectsLoading: boolean;
  projectsError: string | null;
  onRetryProjects: () => void;
  canDeploy: boolean;
  connectionName: string;
  teamSlug: string;
  projectSlug: string;
}

/**
 * Apps › Semantic layers › New (#2427): the wizard's frame with the step on
 * screen, or, once the request was accepted, the creation's progress. Pure:
 * every value and callback comes from useNewBrain.
 */
export function NewBrainWizard(props: NewBrainWizardProps) {
  const { progress, draft, onChange, problems, serverErrors } = props;
  if (progress) return <BrainCreationProgress {...progress} />;
  return (
    <NewBrainScreen
      step={props.step}
      onStep={props.onStep}
      onBack={props.onBack}
      onContinue={props.onContinue}
      continueDisabled={props.continueDisabled}
      busy={props.busy}
      onCancel={props.onCancel}
      denied={props.denied}
    >
      {props.step === 1 ? (
        <BrainSourceStep
          draft={draft}
          onChange={(patch) => onChange({ ...draft, ...patch })}
          connections={props.connections}
          connectionsLoading={props.connectionsLoading}
          connectionsError={props.connectionsError}
          onRetryConnections={props.onRetryConnections}
          problems={problems}
          serverErrors={serverErrors}
        />
      ) : props.step === 2 ? (
        <BrainDetailsStep
          draft={draft}
          onChange={onChange}
          projects={props.projects}
          projectsLoading={props.projectsLoading}
          projectsError={props.projectsError}
          onRetryProjects={props.onRetryProjects}
          canDeploy={props.canDeploy}
          problems={problems}
          serverErrors={serverErrors}
        />
      ) : props.step === 3 ? (
        <BrainSubstrateForm
          mode="create"
          draft={draft.substrate}
          onChange={(substrate) => onChange({ ...draft, substrate })}
          matrix={props.matrix}
          serverError={props.substrateError}
        />
      ) : (
        <BrainReviewStep
          draft={draft}
          connectionName={props.connectionName}
          teamSlug={props.teamSlug}
          projectSlug={props.projectSlug}
          canDeploy={props.canDeploy}
          onEdit={props.onStep}
          pinnedRef={props.pinnedRef}
          serverError={props.generalError}
        />
      )}
    </NewBrainScreen>
  );
}
