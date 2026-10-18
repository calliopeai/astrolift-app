"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { useRouter, useSearchParams } from "next/navigation";
import * as React from "react";

import { usableSourceConnections } from "@/components/wizard/source-connection";
import { useWizardProjects } from "@/components/wizard/use-wizard-projects";
import type {
  CreateBrainMutation,
  CreateBrainMutationVariables,
  GetBrainCreationQuery,
  GetBrainCreationQueryVariables,
  GetBrainEditionQuery,
  GetBrainEditionQueryVariables,
} from "@/graphql/__generated__/operations";
import { CREATE_BRAIN } from "@/graphql/registry/brains.mutations";
import { GET_BRAIN_CREATION, GET_BRAIN_EDITION } from "@/graphql/registry/brains.queries";
import { LIST_SOURCE_CONNECTIONS } from "@/graphql/scm/scm.queries";
import type { AstroliftSourceConnection } from "@/graphql/scm/scm.types";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

import type { SubstrateServerError } from "../BrainSubstrateForm";
import { gatedWrite } from "../gated-write";
import { useBrainCells } from "../use-brain-cells";
import type { NewBrainWizardProps } from "./NewBrainWizard";
import {
  createInput,
  creationFinished,
  type DraftField,
  draftFieldOf,
  draftProblems,
  EMPTY_DRAFT,
  failedStep,
  type NewBrainDraft,
  type NewBrainStep,
  stepOfServerField,
  stepReady,
  withDefaults,
} from "./new-brain-model";

interface ConnectionsResp {
  astroliftSourceConnections: AstroliftSourceConnection[];
}

/** How often a creation in progress is read again. */
export const CREATION_POLL_MS = 2_000;

/**
 * The data half of the create wizard (#2427): the viewer's connections and
 * projects, the draft, `createBrain` (a production deploy asks for a fresh
 * sign-in first; the app-wide prompt handles it, then the request is sent
 * once more), and the creation it returns, read every two seconds until
 * READY or FAILED. The creation's id sits in the URL (`?creation=`), so a
 * reload keeps watching it.
 */
export function useNewBrain(): NewBrainWizardProps {
  const t = useTranslations("brains.wizard");
  const router = useRouter();
  const params = useSearchParams();
  const creationId = params?.get("creation") ?? "";
  const deployAsked = params?.get("deploy") !== "0";
  const perms = useMyPermissions();
  const canCreate = perms.loading || perms.can("app.create");
  const canDeploy = perms.loading || perms.can("app.deploy");

  const connectionsQuery = useQuery<ConnectionsResp>(LIST_SOURCE_CONNECTIONS, {
    fetchPolicy: "cache-and-network",
    skip: Boolean(creationId),
  });
  const connections = React.useMemo(
    () =>
      usableSourceConnections(connectionsQuery.data?.astroliftSourceConnections ?? []).map((c) => ({
        id: c.id,
        name: c.displayName || c.name,
        kind: c.kind,
        accountLogin: c.accountLogin,
      })),
    [connectionsQuery.data]
  );
  const wizardProjects = useWizardProjects();
  const projects = React.useMemo(
    () =>
      wizardProjects.allProjects.map((p) => ({
        id: p.id,
        slug: p.slug,
        name: p.name,
        team: { id: p.team.id, slug: p.team.slug, name: p.team.name },
      })),
    [wizardProjects.allProjects]
  );
  const [step, setStep] = React.useState<NewBrainStep>(1);
  const [own, setOwn] = React.useState<NewBrainDraft>(EMPTY_DRAFT);
  // The edition is read once the ref is chosen (past Source), and the
  // matrix once the substrate is (from Substrate on).
  const editionQuery = useQuery<GetBrainEditionQuery, GetBrainEditionQueryVariables>(
    GET_BRAIN_EDITION,
    {
      variables: { templateRef: own.templateRef.trim() || null },
      skip: Boolean(creationId) || step < 2,
    }
  );
  const edition = editionQuery.data?.astroliftBrainEdition ?? null;
  const matrix = useBrainCells({ skip: Boolean(creationId) || step < 3 });
  const context = {
    connections,
    projects,
    matrix,
    supportsAppSlug: edition?.supportsAppSlug ?? null,
  };
  const draft = withDefaults(own, context);
  const problems = draftProblems(draft, context);
  const [serverErrors, setServerErrors] = React.useState<Partial<Record<DraftField, string>>>({});
  const [substrateError, setSubstrateError] = React.useState<SubstrateServerError | null>(null);
  const [generalError, setGeneralError] = React.useState<string | null>(null);
  // Set once a request was accepted: the values it was made from, so a
  // failed creation can go back to them.
  const [sent, setSent] = React.useState<NewBrainDraft | null>(null);

  // The list reads again when it mounts (cache-and-network), so nothing
  // here refetches a query that is not on screen.
  const [create, createState] = useMutation<CreateBrainMutation, CreateBrainMutationVariables>(
    CREATE_BRAIN
  );

  const change = (next: NewBrainDraft) => {
    setOwn(next);
    // An edit answers the server's complaint about that part.
    setServerErrors({});
    setSubstrateError(null);
    setGeneralError(null);
  };

  async function submit() {
    const outcome = await gatedWrite(
      async () =>
        (await create({ variables: { input: createInput(draft, { canDeploy }) } })).data
          ?.createBrain,
      t("createFailed")
    );
    if (outcome.ok) {
      setSent(draft);
      const deploys = canDeploy && draft.deploy;
      router.replace(
        `/brains/new?creation=${encodeURIComponent(outcome.data.id)}${deploys ? "" : "&deploy=0"}`,
        { scroll: false }
      );
      return;
    }
    const field = draftFieldOf(outcome.field);
    if (field === "substrate")
      setSubstrateError({ field: outcome.field, message: outcome.message });
    else if (field) setServerErrors({ [field]: outcome.message });
    else setGeneralError(outcome.message);
    setStep(stepOfServerField(outcome.field));
  }

  const creationQuery = useQuery<GetBrainCreationQuery, GetBrainCreationQueryVariables>(
    GET_BRAIN_CREATION,
    { variables: { id: creationId }, skip: !creationId, fetchPolicy: "cache-and-network" }
  );
  const creation = creationQuery.data?.astroliftBrainCreation ?? null;
  const finished = creation ? creationFinished(creation.state) : false;
  const readFailed = Boolean(creationQuery.error);
  const { refetch } = creationQuery;
  // Poll by timer rather than pollInterval: the cadence depends on the
  // answer, and the timer only reads, it never writes state.
  React.useEffect(() => {
    if (!creationId || finished || readFailed) return;
    const timer = window.setInterval(() => {
      void refetch().catch(() => {});
    }, CREATION_POLL_MS);
    return () => window.clearInterval(timer);
  }, [creationId, finished, readFailed, refetch]);
  const previousState = creationQuery.previousData?.astroliftBrainCreation?.state ?? null;
  const startOver = () => {
    setSent(null);
    setOwn(EMPTY_DRAFT);
    setStep(1);
    router.replace("/brains/new", { scroll: false });
  };

  const connectionName =
    connections.find((c) => c.id === draft.connectionId)?.name ?? draft.connectionId;
  const project = projects.find((p) => p.id === draft.projectId);

  return {
    progress: creationId
      ? {
          creation,
          loading: creationQuery.loading && !creation,
          error: creationQuery.error?.message ?? null,
          onRetry: () => {
            void refetch().catch(() => {});
          },
          deploy: deployAsked,
          failed: creation?.state === "FAILED" ? failedStep(creation, previousState) : null,
          onEdit: sent
            ? () => {
                setOwn(sent);
                setStep(4);
                router.replace("/brains/new", { scroll: false });
              }
            : undefined,
          onStartOver: startOver,
        }
      : null,
    denied: !canCreate,
    step,
    onStep: setStep,
    onBack: step > 1 ? () => setStep((step - 1) as NewBrainStep) : undefined,
    onContinue: () => {
      if (step < 4) setStep((step + 1) as NewBrainStep);
      else void submit();
    },
    continueDisabled:
      step < 4
        ? !stepReady(step, problems)
        : !([1, 2, 3] as const).every((s) => stepReady(s, problems)),
    busy: createState.loading,
    onCancel: () => router.push("/brains"),
    draft,
    onChange: change,
    problems,
    serverErrors,
    substrateError,
    matrix,
    pinnedRef: edition && !own.templateRef.trim() ? edition.ref : null,
    generalError,
    connections,
    connectionsLoading: connectionsQuery.loading && !connectionsQuery.data,
    connectionsError:
      connectionsQuery.error && !connectionsQuery.data ? connectionsQuery.error.message : null,
    onRetryConnections: () => {
      void connectionsQuery.refetch().catch(() => {});
    },
    projects,
    projectsLoading: wizardProjects.loading && projects.length === 0,
    projectsError:
      wizardProjects.error && projects.length === 0 ? wizardProjects.error.message : null,
    onRetryProjects: () => {
      void wizardProjects.refetch().catch(() => {});
    },
    canDeploy,
    connectionName,
    teamSlug: project?.team.slug ?? "",
    projectSlug: project?.slug ?? "",
  };
}
