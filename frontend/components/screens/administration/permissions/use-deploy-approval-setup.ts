"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import * as React from "react";

import type {
  DeployApprovalAppsQuery,
  ListTeamsQuery,
  UpdateAppDeployApprovalMutation,
} from "@/graphql/__generated__/operations";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { LIST_TEAMS } from "@/graphql/identity/identity.queries";
import { DEPLOY_APPROVAL_APPS } from "@/graphql/permissions/astrolift.queries";
import { UPDATE_APP_DEPLOY_APPROVAL } from "@/graphql/permissions/deploy-approval.mutations";

import type {
  DeployApprovalApp,
  DeployApprovalChange,
  DeployApprovalSetupProps,
} from "./DeployApprovalSetup";

/**
 * The input `updateApp` takes for a guided approval change: only the
 * deploy-approval fields, against the version read. Turning it on names a
 * team, which replaces any specific approver people (the two are exclusive).
 */
export function deployApprovalInput(app: DeployApprovalApp, change: DeployApprovalChange) {
  if (!change.requiresApproval) {
    return { id: app.id, requiresApproval: false, ifMatchVersion: app.version };
  }
  return {
    id: app.id,
    requiresApproval: true,
    approverTeamId: change.approverTeamId,
    minimumApprovals: change.minimumApprovals,
    ...(app.approverUserIds.length > 0 ? { approverUserIds: [] } : {}),
    ifMatchVersion: app.version,
  };
}

/** The data half of `DeployApprovalSetup` (#2272). */
export function useDeployApprovalSetup(): DeployApprovalSetupProps {
  const t = useTranslations("adminPresets.approval");
  const { org } = useActiveOrg();
  const apps = useQuery<DeployApprovalAppsQuery>(DEPLOY_APPROVAL_APPS, {
    fetchPolicy: "cache-and-network",
  });
  const teams = useQuery<ListTeamsQuery>(LIST_TEAMS, { fetchPolicy: "cache-and-network" });
  const [update] = useMutation<UpdateAppDeployApprovalMutation>(UPDATE_APP_DEPLOY_APPROVAL);

  const rows = React.useMemo<DeployApprovalApp[] | null>(
    () =>
      apps.data
        ? apps.data.astroliftApps.map((a) => ({
            id: a.id,
            slug: a.slug,
            name: a.name,
            requiresApproval: a.requiresApproval,
            minimumApprovals: a.minimumApprovals,
            approverTeamId: a.approverTeamId ?? null,
            approverUserIds: a.approverUserIds,
            version: a.version,
            canUpdate: a.viewerPermissions.includes("app.update"),
          }))
        : null,
    [apps.data]
  );

  return {
    apps: rows,
    // The install-wide list narrowed to the active org, as the app's approver team must be.
    teams: (teams.data?.astroliftTeams ?? [])
      .filter((x) => !x.deletedAt && org && x.organization?.id === org.id)
      .map((x) => ({ id: x.id, name: x.name })),
    loading: apps.loading && !apps.data,
    error: apps.error?.message ?? null,
    onRetry: () => void apps.refetch().catch(() => {}),
    onSave: async (app, change) => {
      try {
        const { data } = await update({ variables: { input: deployApprovalInput(app, change) } });
        const r = data?.updateApp;
        if (!r?.ok) return r?.errors?.[0]?.message ?? t("saveFailed");
        await apps.refetch().catch(() => {});
        return null;
      } catch (err) {
        return err instanceof Error ? err.message : t("saveFailed");
      }
    },
  };
}
