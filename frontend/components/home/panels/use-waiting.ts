"use client";

import { useTranslations } from "next-intl";

import { useMutation, useQuery } from "@apollo/client/react";
import { toast } from "sonner";

import { APPROVE_DEPLOYMENT } from "@/graphql/lifecycle/lifecycle.mutations";
import { LIST_DEPLOYMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftDeployment } from "@/graphql/lifecycle/lifecycle.types";
import { LIST_SECRET_CHANGE_PROPOSALS } from "@/graphql/services/services.queries";
import type { AstroliftSecretChangeProposal } from "@/graphql/services/services.types";
import { usePendingHumanGates } from "@/graphql/workflows/tiered.hooks";
import type { PendingHumanGate } from "@/graphql/workflows/tiered.types";

import {
  combineReads,
  HOME_POLL_MS,
  type HomeRead,
  readState,
  useHomeDeployments,
  usePanelAccess,
} from "./home-reads";
import { deployHref, deployShort, workflowRunHref } from "./apps-agents-model";
import type { WaitingItem, WaitingPanelViewProps } from "./WaitingPanel";

interface ProposalsResp {
  astroliftSecretChangeProposals: AstroliftSecretChangeProposal[];
}

interface ApproveResp {
  approveDeployment: {
    ok: boolean;
    errors: { code: string; message: string }[];
    data: AstroliftDeployment | null;
  };
}

/** A deploy waiting on approval that the viewer did not trigger (they cannot approve their own). */
export function deployItem(
  d: AstroliftDeployment,
  t?: ReturnType<typeof useTranslations<"home">>
): WaitingItem {
  const env = d.environmentName || (t ? t("operations.everyEnvironment") : "every environment");
  return {
    key: `deploy:${d.id}`,
    kind: "deploy",
    title: t
      ? t("operations.deployTitle", { app: d.registeredAppSlug, environment: env })
      : `${d.registeredAppSlug} to ${env}`,
    detail: t
      ? t("operations.deployDetail", {
          image: d.imageTag || deployShort(d),
          received: d.approvalsReceived,
          required: d.approvalsRequired,
        })
      : `${d.imageTag || deployShort(d)} · ${d.approvalsReceived}/${d.approvalsRequired} approvals`,
    at: d.createdAt,
    href: deployHref(d.id),
    approveId: d.id,
  };
}

export function gateItem(
  g: PendingHumanGate,
  t?: ReturnType<typeof useTranslations<"home">>
): WaitingItem {
  return {
    key: `gate:${g.runGuid}:${g.executionId}`,
    kind: "gate",
    title: `${g.definitionName || g.definitionSlug} · ${g.stageRole || (t ? t("operations.humanGate") : "human gate")}`,
    detail: g.stageApprovers.length
      ? t
        ? t("operations.approvers", { approvers: g.stageApprovers.join(", ") })
        : `Approvers: ${g.stageApprovers.join(", ")}`
      : t
        ? t("operations.anyApprover")
        : "Any approver may decide",
    at: g.startedAt ?? "",
    href: workflowRunHref(g.definitionSlug, g.runGuid),
  };
}

export function secretItem(
  p: AstroliftSecretChangeProposal,
  t?: ReturnType<typeof useTranslations<"home">>
): WaitingItem {
  const env = p.environmentName || (t ? t("operations.appWide") : "app-wide");
  return {
    key: `secret:${p.id}`,
    kind: "secret",
    title: t
      ? t("operations.secretTitle", { app: p.registeredAppSlug, environment: env })
      : `${p.registeredAppSlug} secrets · ${env}`,
    detail: t
      ? t("operations.secretDetail", {
          operation: p.proposerDisplayName
            ? t("operations.secretBy", { operation: p.op, name: p.proposerDisplayName })
            : p.op,
          received: p.approvalsCount,
          required: p.requiredApproverCount,
        })
      : `${p.op}${p.proposerDisplayName ? ` by ${p.proposerDisplayName}` : ""} · ${p.approvalsCount}/${p.requiredApproverCount} approvals`,
    at: p.createdAt,
    href: `/approvals/secret/${encodeURIComponent(p.id)}`,
  };
}

/**
 * Waiting on you's data: the approvals queue's deployments read, the
 * org-wide pending gates (which the server narrows to gates the caller may
 * decide) and pending secret requests, each read only when the viewer can
 * act on that kind. Tool approvals have no query yet (see needsBackend).
 */
export function useWaiting(): Omit<WaitingPanelViewProps, "panel"> {
  const t = useTranslations("home");
  const { canView, can } = usePanelAccess();
  const deploysOn = canView("apps") && can("app.approve_deploy");
  const gatesOn = canView("workflows");
  const secretsOn = can("secret.approve");

  const deploys = useHomeDeployments({ skip: !deploysOn });
  const gates = usePendingHumanGates({ pollInterval: HOME_POLL_MS, skip: !gatesOn });
  const proposals = useQuery<ProposalsResp>(LIST_SECRET_CHANGE_PROPOSALS, {
    variables: { appSlug: null, status: "pending" },
    fetchPolicy: "cache-and-network",
    pollInterval: HOME_POLL_MS,
    skip: !secretsOn,
  });

  const [approve, approveState] = useMutation<ApproveResp>(APPROVE_DEPLOYMENT, {
    refetchQueries: [{ query: LIST_DEPLOYMENTS, variables: { limit: 100 } }],
  });

  const items: WaitingItem[] = [
    ...(deploysOn
      ? deploys.deployments
          .filter((d) => d.status === "pending_approval" && !d.triggeredByMe)
          .map((d) => deployItem(d, t))
      : []),
    ...(gatesOn ? gates.gates.map((g) => gateItem(g, t)) : []),
    ...(secretsOn
      ? (proposals.data?.astroliftSecretChangeProposals ?? []).map((p) => secretItem(p, t))
      : []),
  ];

  const sources: HomeRead[] = [];
  if (deploysOn) sources.push(deploys);
  if (gatesOn) sources.push(readState(gates, gates.gates.length > 0));
  if (secretsOn) sources.push(readState(proposals, Boolean(proposals.data)));

  return {
    items,
    ...combineReads(sources, items.length > 0),
    approving: approveState.loading,
    onApprove: async (id: string) => {
      const { data } = await approve({ variables: { input: { id } } });
      const result = data?.approveDeployment;
      if (!result?.ok) throw new Error(result?.errors[0]?.message ?? t("operations.approveFailed"));
      const status = result.data?.status;
      toast.success(
        status
          ? t("operations.approved", {
              status: t.has(`status.${status}`) ? t(`status.${status}`) : status,
            })
          : t("status.approved")
      );
    },
  };
}
