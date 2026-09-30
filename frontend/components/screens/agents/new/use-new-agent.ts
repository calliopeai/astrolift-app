"use client";

import { useMutation } from "@apollo/client/react";
import { useRouter } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { REGISTER_AGENT_REPO } from "@/graphql/agents/agents.mutations";
import { LIST_AGENT_FLEET, LIST_AGENT_WORKLOADS } from "@/graphql/agents/agents.queries";
import type {
  AstroliftDiscoveredAgentManifest,
  AstroliftRegisterAgentRepoResult,
  AstroliftRegisteredAgent,
} from "@/graphql/agents/agents.types";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { useWizardProjects } from "@/components/wizard/use-wizard-projects";
import type { MutationResult } from "@/graphql/identity/identity.types";
import type { SourceKind } from "@/graphql/registry/registry.types";
import type { ScmConnectionKind } from "@/graphql/scm/scm.types";

import type { SideEffectStep } from "./AgentReviewSubmitStep";
import type { NewAgentStep } from "./NewAgentPage";

export interface WizardState {
  step: NewAgentStep;

  // Source: repository
  connectionId: string;
  connectionKind: ScmConnectionKind | "";
  sourceKind: SourceKind;
  sourceRepo: string;
  sourceUrl: string;
  defaultBranch: string;
  deployBranch: string;
  ref: string;

  // Source: agents found
  scanned: boolean;
  scanError: string | null;
  discoveredAgents: AstroliftDiscoveredAgentManifest[];
  // Client-side preview selection. registerAgentRepo has no per-manifest
  // filter, so this drives the preview only: every NEW agent in the repo is
  // registered regardless (see useAgentDiscoveryStep).
  selectedManifestPaths: string[];

  // Configure: project
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

/** The parts that report their own validity; a step continues once all of its parts are valid. */
export type NewAgentPart = "repo" | "discover" | "project";

const PART_ERROR: Record<NewAgentPart, string> = {
  repo: "Pick a source connection and a repository to scan.",
  discover: "The scan has to find at least one agent that is not registered yet.",
  project: "Pick the project the agents will be registered under.",
};

/** The in-place error for each part of the step on screen, once Continue was pressed there. */
export function partErrors(
  step: NewAgentStep,
  valid: Record<NewAgentPart, boolean>,
  attempted: boolean,
  hasRepo: boolean
): Partial<Record<NewAgentPart, string>> {
  if (!attempted) return {};
  const parts: NewAgentPart[] =
    step === 1 ? (hasRepo ? ["repo", "discover"] : ["repo"]) : step === 2 ? ["project"] : [];
  const out: Partial<Record<NewAgentPart, string>> = {};
  for (const p of parts) if (!valid[p]) out[p] = PART_ERROR[p];
  return out;
}

/** Whether a step can be left forward. Source needs a repo and a scan with a new agent. */
export function stepValid(
  step: NewAgentStep,
  valid: Record<NewAgentPart, boolean>,
  hasRepo: boolean
): boolean {
  if (step === 1) return hasRepo && valid.repo && valid.discover;
  if (step === 2) return valid.project;
  return true;
}

/**
 * The New agent flow's state machine: one `WizardState`, per-part validity
 * reported by each step's hook, errors shown in place once Continue was
 * pressed on a step, and the `registerAgentRepo` submit with its side-effect
 * plan. On success it opens the Agents list filtered to the destination
 * project. The data half of NewAgentPage.
 */
export function useNewAgent() {
  const router = useRouter();
  const mounted = React.useRef(true);
  React.useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const { org } = useActiveOrg();
  const orgId = org?.id ?? "";
  const [state, setState] = React.useState<WizardState>(initialWizardState);
  const [valid, setValidState] = React.useState<Record<NewAgentPart, boolean>>({
    repo: false,
    discover: false,
    project: false,
  });
  const [attempted, setAttempted] = React.useState<Record<NewAgentStep, boolean>>({
    1: false,
    2: false,
    3: false,
  });
  const [submitting, setSubmitting] = React.useState(false);
  const [submitError, setSubmitError] = React.useState<string | null>(null);
  const [sideEffects, setSideEffects] = React.useState<SideEffectStep[]>([]);
  const [registeredAgents, setRegisteredAgents] = React.useState<AstroliftRegisteredAgent[]>([]);
  const [confirmCancel, setConfirmCancel] = React.useState(false);

  // The destination project's slug (the success redirect's filter and the
  // scoped refetch) and its `team/project` label for Review.
  const projects = useWizardProjects();
  const picked = projects.allProjects.find((p) => p.id === state.projectId);
  const projectSlug = picked?.slug ?? "";
  const projectLabel = picked ? `${picked.team.slug}/${picked.slug}` : "";

  const [registerAgentRepo] = useMutation<{
    registerAgentRepo: MutationResult<AstroliftRegisterAgentRepoResult>;
  }>(REGISTER_AGENT_REPO, {
    // The fleet roll-up and the destination project's list, so the new
    // agents are on /agents when it opens.
    refetchQueries: [
      { query: LIST_AGENT_FLEET, variables: { orgId } },
      ...(projectSlug ? [{ query: LIST_AGENT_WORKLOADS, variables: { orgId, projectSlug } }] : []),
    ],
    awaitRefetchQueries: true,
  });

  const setValid = React.useCallback((part: NewAgentPart, next: boolean) => {
    setValidState((prev) => (prev[part] === next ? prev : { ...prev, [part]: next }));
  }, []);

  const goTo = React.useCallback((step: NewAgentStep) => {
    setState((s) => ({ ...s, step }));
  }, []);

  const hasRepo = state.sourceRepo !== "";

  async function submit() {
    if (!picked || !orgId) {
      setAttempted((a) => ({ ...a, 2: true }));
      goTo(2);
      return;
    }
    setSubmitting(true);
    setSubmitError(null);
    setSideEffects([{ key: "register", label: "Register agents", status: "running" }]);
    const update = (patch: Partial<SideEffectStep>) =>
      setSideEffects((prev) => prev.map((s) => (s.key === "register" ? { ...s, ...patch } : s)));

    try {
      await projects.confirmDestination(state.projectId);
      if (!mounted.current) return;
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
      if (!mounted.current) return;
      const result = data?.registerAgentRepo;
      if (!result?.ok || !result.data) {
        const msg = result?.errors?.[0]?.message ?? "Registration failed";
        update({ status: "failed", error: msg });
        setSubmitError(msg);
        setSubmitting(false);
        return;
      }
      const agents = result.data.agents;
      setRegisteredAgents(agents);
      update({ status: "done" });
      const created = agents.filter((a) => a.created).length;
      const matched = agents.length - created;
      toast.success(
        `Registered ${created} new agent${created === 1 ? "" : "s"}` +
          (matched > 0 ? ` (${matched} already existed)` : "")
      );
      router.push(projectSlug ? `/agents?project=${encodeURIComponent(projectSlug)}` : "/agents");
    } catch (err) {
      if (!mounted.current) return;
      const msg = err instanceof Error ? err.message : "Registration failed";
      update({ status: "failed", error: msg });
      setSubmitError(msg);
      setSubmitting(false);
    }
  }

  function onContinue() {
    if (state.step === 3) {
      void submit();
      return;
    }
    if (!stepValid(state.step, valid, hasRepo)) {
      setAttempted((a) => ({ ...a, [state.step]: true }));
      return;
    }
    goTo((state.step + 1) as NewAgentStep);
  }

  function onCancel() {
    if (state.connectionId !== "" || state.sourceRepo !== "") setConfirmCancel(true);
    else router.push("/agents");
  }

  return {
    state,
    setState,
    setValid,
    errors: partErrors(state.step, valid, attempted[state.step], hasRepo),
    hasRepo,
    projectLabel,
    submitting,
    submitError,
    sideEffects,
    registeredAgents,
    onStep: goTo,
    onBack:
      state.step > 1 && !submitting ? () => goTo((state.step - 1) as NewAgentStep) : undefined,
    onContinue,
    continueLabel:
      state.step === 3 ? (submitting ? "Registering..." : "Register agents") : "Continue",
    onCancel: submitting ? undefined : onCancel,
    confirmCancel,
    setConfirmCancel,
    onDiscard: () => router.push("/agents"),
  };
}
