"use client";

import { useTranslations } from "next-intl";

import { useActiveOrg } from "@/graphql/identity/identity.hooks";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { NewAgentPage, NewAgentSection } from "@/components/screens/agents/new/NewAgentPage";
import { useNewAgent } from "@/components/screens/agents/new/use-new-agent";

import { DiscoveryStep } from "./steps/DiscoveryStep";
import { ProjectStep } from "./steps/ProjectStep";
import { RepoPickerStep } from "./steps/RepoPickerStep";
import { ReviewSubmitStep } from "./steps/ReviewSubmitStep";

/**
 * Agents › New agent: 1 Source (repository, then the agents a scan finds),
 * 2 Configure (project), 3 Review. The hook holds the flow; each step part is
 * a container that reports its own validity.
 */
export function WizardClient() {
  const { org } = useActiveOrg();
  return <ScopedWizardClient key={org?.id ?? "unresolved"} />;
}

function ScopedWizardClient() {
  const t = useTranslations("agentWizard");
  const flow = useNewAgent();
  const { state, setState, setValid, errors } = flow;

  return (
    <NewAgentPage
      step={state.step}
      onStep={flow.onStep}
      onBack={flow.onBack}
      onContinue={flow.onContinue}
      continueLabel={flow.continueLabel}
      busy={flow.submitting}
      onCancel={flow.onCancel}
    >
      {state.step === 1 && (
        <>
          <NewAgentSection title={t("sections.repository")} error={errors.repo}>
            <RepoPickerStep
              state={state}
              setState={setState}
              setValid={(v) => setValid("repo", v)}
            />
          </NewAgentSection>
          {flow.hasRepo && (
            <NewAgentSection
              title={t("sections.agentsFound")}
              description={t("sections.agentsFoundDescription")}
              error={errors.discover}
            >
              <DiscoveryStep
                state={state}
                setState={setState}
                setValid={(v) => setValid("discover", v)}
              />
            </NewAgentSection>
          )}
        </>
      )}
      {state.step === 2 && (
        <NewAgentSection title={t("sections.project")} error={errors.project}>
          <ProjectStep state={state} setState={setState} setValid={(v) => setValid("project", v)} />
        </NewAgentSection>
      )}
      {state.step === 3 && (
        <ReviewSubmitStep
          state={state}
          projectLabel={flow.projectLabel}
          onJumpToStep={flow.onStep}
          submitting={flow.submitting}
          submitError={flow.submitError}
          sideEffects={flow.sideEffects}
          registeredAgents={flow.registeredAgents}
        />
      )}
      <ConfirmDialog
        open={flow.confirmCancel}
        onOpenChange={flow.setConfirmCancel}
        title={t("discard.title")}
        description={t("discard.description")}
        confirmLabel={t("discard.confirm")}
        destructive
        onConfirm={flow.onDiscard}
      />
    </NewAgentPage>
  );
}

export default WizardClient;
