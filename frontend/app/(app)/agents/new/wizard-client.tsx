"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useRouter } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { REGISTER_AGENT_REPO } from "@/graphql/agents/agents.mutations";
import { LIST_AGENT_FLEET, LIST_AGENT_WORKLOADS } from "@/graphql/agents/agents.queries";
import type {
  AstroliftDiscoveredAgentManifest,
  AstroliftRegisterAgentRepoResult,
  AstroliftRegisteredAgent,
} from "@/graphql/agents/agents.types";
import { LIST_PROJECTS } from "@/graphql/identity/identity.queries";
import type { AstroliftProject } from "@/graphql/identity/identity.types";
import type { MutationResult } from "@/graphql/identity/identity.types";
import type { SourceKind } from "@/graphql/registry/registry.types";
import type { ScmConnectionKind } from "@/graphql/scm/scm.types";
import { getActiveOrgGuid } from "@/lib/identity/active-org";

import { WizardShell, type WizardStep } from "./components/WizardShell";
import { DiscoveryStep } from "./steps/DiscoveryStep";
import { ProjectStep } from "./steps/ProjectStep";
import { RepoPickerStep } from "./steps/RepoPickerStep";
import { ReviewSubmitStep, type SideEffectStep } from "./steps/ReviewSubmitStep";

// Four steps: Repo → Discover → Project → Review. The register-app wizard's
// Manifest / Details / Strategy steps are dropped (see PR-8 notes): agents
// derive name/slug/run-mode from their manifests, and registerAgentRepo takes
// no trigger/approval config — only a destination projectId.
type StepNumber = 1 | 2 | 3 | 4;

export interface WizardState {
  step: StepNumber;

  // Step 1 — repo pick
  connectionId: string;
  connectionKind: ScmConnectionKind | "";
  sourceKind: SourceKind;
  sourceRepo: string;
  sourceUrl: string;
  defaultBranch: string;
  deployBranch: string;
  ref: string;

  // Step 2 — discovery
  scanned: boolean;
  scanError: string | null;
  discoveredAgents: AstroliftDiscoveredAgentManifest[];
  // Client-side preview selection. NOTE: registerAgentRepo has no per-manifest
  // filter, so this drives the preview UI only — every NEW agent in the repo
  // is registered regardless. See README in PR-8 / DiscoveryStep docstring.
  selectedManifestPaths: string[];

  // Step 3 — project
  projectId: string;
}

export function initialWizardState(): WizardState {
  return {
    step: 1,
    connectionId: "",
    connectionKind: "",
    sourceKind: "github",
    sourceRepo: "",
    sourceUrl: "",
    defaultBranch: "main",
    deployBranch: "main",
    ref: "main",
    scanned: false,
    scanError: null,
    discoveredAgents: [],
    selectedManifestPaths: [],
    projectId: "",
  };
}

const STEPS: WizardStep[] = [
  {
    key: "repo",
    label: "Repo",
    description: "Pick a source connection, a repository, and the branch to scan.",
  },
  {
    key: "discover",
    label: "Discover",
    description: "Scan the repo for agent manifests and review what will be registered.",
  },
  {
    key: "project",
    label: "Project",
    description: "Choose the project the discovered agents will live under.",
  },
  {
    key: "review",
    label: "Review",
    description: "Final summary and the things we'll do on submit.",
  },
];

interface ProjectsResp {
  astroliftProjects: AstroliftProject[];
}

/**
 * Top-level state machine for the Register-agent-repo wizard. Mirrors the
 * register-app wizard: a single `useState<WizardState>`; every step gets
 * `state` + `setState` and reports its own validity via `setValid`; the shell
 * binds Back/Next on per-step validity. The final submit calls
 * `registerAgentRepo`, drives the side-effect plan, and redirects to the
 * `/agents` registry list scoped to the destination project.
 */
export function WizardClient() {
  const router = useRouter();
  const orgId = getActiveOrgGuid() ?? "";
  const [state, setState] = React.useState<WizardState>(initialWizardState);
  const [stepValid, setStepValid] = React.useState<Record<StepNumber, boolean>>({
    1: false,
    2: false,
    3: false,
    4: true,
  });
  const [submitting, setSubmitting] = React.useState(false);
  const [submitError, setSubmitError] = React.useState<string | null>(null);
  const [sideEffects, setSideEffects] = React.useState<SideEffectStep[]>([]);
  const [registeredAgents, setRegisteredAgents] = React.useState<AstroliftRegisteredAgent[]>([]);

  // Project list — used to resolve the destination project's slug (for the
  // success redirect + scoped refetch — the registry list is scoped via
  // ?project=<slug>) and its human label (for the review card).
  const projectsQuery = useQuery<ProjectsResp>(LIST_PROJECTS, { fetchPolicy: "cache-first" });
  const pickedProject = React.useMemo(
    () => projectsQuery.data?.astroliftProjects.find((p) => p.id === state.projectId),
    [projectsQuery.data, state.projectId]
  );
  const projectSlug = pickedProject?.slug ?? "";
  const projectLabel = pickedProject ? `${pickedProject.team.slug}/${pickedProject.slug}` : "";

  const [registerAgentRepo] = useMutation<{
    registerAgentRepo: MutationResult<AstroliftRegisterAgentRepoResult>;
  }>(REGISTER_AGENT_REPO, {
    // Refetch both the fleet roll-up and the destination project's scoped list
    // so the newly-registered agents appear on /agents immediately (PR-7).
    refetchQueries: [
      { query: LIST_AGENT_FLEET, variables: { orgId } },
      ...(projectSlug ? [{ query: LIST_AGENT_WORKLOADS, variables: { orgId, projectSlug } }] : []),
    ],
    awaitRefetchQueries: true,
  });

  // ---- Step movement ----

  const goTo = React.useCallback((next: StepNumber) => {
    setState((s) => ({ ...s, step: next }));
  }, []);

  const goBack = React.useCallback(() => {
    setState((s) => ({ ...s, step: Math.max(1, s.step - 1) as StepNumber }));
  }, []);

  const goNext = React.useCallback(() => {
    setState((s) => ({ ...s, step: Math.min(4, s.step + 1) as StepNumber }));
  }, []);

  const setValid = React.useCallback((step: StepNumber, valid: boolean) => {
    setStepValid((prev) => (prev[step] === valid ? prev : { ...prev, [step]: valid }));
  }, []);

  // ---- Cancel ----

  const [confirmCancel, setConfirmCancel] = React.useState(false);

  const handleCancel = React.useCallback(() => {
    const dirty = state.connectionId !== "" || state.sourceRepo !== "";
    if (dirty) {
      setConfirmCancel(true);
      return;
    }
    router.push("/agents?tab=registry");
  }, [router, state]);

  // ---- Submit ----

  async function submit() {
    if (!state.projectId) {
      toast.error("Pick a project before submitting.");
      return;
    }
    setSubmitting(true);
    setSubmitError(null);

    const plan: SideEffectStep[] = [
      { key: "register", label: "Register agents", status: "pending" },
    ];
    setSideEffects(plan);

    const update = (key: string, patch: Partial<SideEffectStep>) =>
      setSideEffects((prev) => prev.map((s) => (s.key === key ? { ...s, ...patch } : s)));

    try {
      update("register", { status: "running" });
      const { data } = await registerAgentRepo({
        variables: {
          input: {
            projectId: state.projectId,
            sourceRepo: state.sourceRepo.trim(),
            sourceKind: state.sourceKind,
            sourceUrl: state.sourceUrl.trim() || null,
            ref: state.ref.trim() || state.defaultBranch.trim() || "main",
            defaultBranch: state.defaultBranch.trim() || "main",
            deployBranch: state.deployBranch.trim() || state.defaultBranch.trim() || "main",
          },
        },
      });
      const result = data?.registerAgentRepo;
      if (!result?.ok || !result.data) {
        const msg = result?.errors?.[0]?.message ?? "Registration failed";
        update("register", { status: "failed", error: msg });
        setSubmitError(msg);
        setSubmitting(false);
        return;
      }
      const agents = result.data.agents;
      setRegisteredAgents(agents);
      update("register", { status: "done" });

      const createdCount = agents.filter((a) => a.created).length;
      const matchedCount = agents.length - createdCount;
      toast.success(
        `Registered ${createdCount} new agent${createdCount === 1 ? "" : "s"}` +
          (matchedCount > 0 ? ` (${matchedCount} already existed)` : "")
      );

      const qs = new URLSearchParams({ tab: "registry" });
      if (projectSlug) qs.set("project", projectSlug);
      router.push(`/agents?${qs.toString()}`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Registration failed";
      update("register", { status: "failed", error: msg });
      setSubmitError(msg);
      setSubmitting(false);
    }
  }

  // ---- Step body + footer-binding ----

  const isLast = state.step === 4;
  const canGoNext = stepValid[state.step] ?? false;

  return (
    <WizardShell
      step={state.step}
      steps={STEPS}
      onStepClick={(idx) => goTo(idx as StepNumber)}
      onBack={state.step > 1 && !submitting ? goBack : undefined}
      onNext={isLast ? submit : canGoNext ? goNext : undefined}
      onCancel={!submitting ? handleCancel : undefined}
      nextLabel={isLast ? (submitting ? "Submitting…" : "Register agents") : "Next"}
      nextDisabled={!canGoNext}
      nextLoading={submitting}
    >
      {state.step === 1 && (
        <RepoPickerStep state={state} setState={setState} setValid={(v) => setValid(1, v)} />
      )}
      {state.step === 2 && (
        <DiscoveryStep state={state} setState={setState} setValid={(v) => setValid(2, v)} />
      )}
      {state.step === 3 && (
        <ProjectStep state={state} setState={setState} setValid={(v) => setValid(3, v)} />
      )}
      {state.step === 4 && (
        <ReviewSubmitStep
          state={state}
          projectLabel={projectLabel}
          steps={STEPS}
          onJumpToStep={(idx) => goTo(idx as StepNumber)}
          submitting={submitting}
          submitError={submitError}
          sideEffects={sideEffects}
          registeredAgents={registeredAgents}
        />
      )}
      <ConfirmDialog
        open={confirmCancel}
        onOpenChange={setConfirmCancel}
        title="Discard wizard progress?"
        description="The repo selection and discovered agents you've built so far are not saved anywhere. Closing the wizard now means starting over."
        confirmLabel="Discard and exit"
        destructive
        onConfirm={() => {
          router.push("/agents?tab=registry");
        }}
      />
    </WizardShell>
  );
}

export default WizardClient;
