"use client";

import { GitBranchIcon, PlugIcon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { TemporaryAppFields } from "@/components/screens/apps/TemporaryAppFields";
import {
  temporaryAppValid,
  type TemporaryAppPolicyState,
} from "@/components/screens/apps/temporary-app-model";
import type { AstroliftRemoteRepoList, AstroliftSourceConnection } from "@/graphql/scm/scm.types";

import { registersApp, type OnboardingWizardState } from "./types";

interface Props {
  state: OnboardingWizardState;
  setState: React.Dispatch<React.SetStateAction<OnboardingWizardState>>;
  /** The organization's temporary-app policy, for the picked repo's app lifetime. */
  temporary: TemporaryAppPolicyState;
}

/** The repo step's data: source connections, and the repos of the chosen one. */
export interface RepoStepData {
  connections: AstroliftSourceConnection[];
  connectionsLoading: boolean;
  repos: AstroliftRemoteRepoList | null;
  reposLoading: boolean;
}

export function RepoStep({
  state,
  setState,
  temporary,
  connections: availableConnections,
  connectionsLoading,
  repos: repoList,
  reposLoading,
}: Props & RepoStepData) {
  const t = useTranslations("onboarding.repo");

  // When connections load, default to the first one so the operator
  // doesn't see an empty <select> placeholder when they actually
  // have one wired up. Only seed when the field is still empty so
  // we don't clobber a manual switch.
  React.useEffect(() => {
    if (state.sourceConnectionId) return;
    if (availableConnections.length === 0) return;
    setState((prev) =>
      prev.sourceConnectionId ? prev : { ...prev, sourceConnectionId: availableConnections[0].id }
    );
  }, [availableConnections, state.sourceConnectionId, setState]);

  if (connectionsLoading && availableConnections.length === 0) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-9 w-full" />
        <Skeleton className="h-9 w-full" />
      </div>
    );
  }

  if (availableConnections.length === 0) {
    return (
      <div className="flex flex-col gap-4">
        <div className="flex items-start gap-3">
          <div className="bg-primary/10 text-primary inline-flex size-9 items-center justify-center rounded-md">
            <PlugIcon className="size-4" />
          </div>
          <div>
            <p className="text-sm font-medium">{t("noConnectionsTitle")}</p>
            <p className="text-muted-foreground mt-1 text-sm">{t("noConnectionsDescription")}</p>
          </div>
        </div>
        <Button asChild variant="outline" className="self-start">
          <Link href="/settings/source-providers">{t("connectCta")}</Link>
        </Button>
        <p className="text-muted-foreground text-xs">{t("skipNote")}</p>
      </div>
    );
  }

  const reposList = repoList?.repos ?? [];
  const repoErrorCode = repoList?.errorCode ?? null;

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-start gap-3">
        <div className="bg-primary/10 text-primary inline-flex size-9 items-center justify-center rounded-md">
          <GitBranchIcon className="size-4" />
        </div>
        <div>
          <p className="text-sm font-medium">{t("greeting")}</p>
          <p className="text-muted-foreground mt-1 text-sm">{t("description")}</p>
        </div>
      </div>

      <div className="grid gap-2">
        <Label htmlFor="ob-connection">{t("connectionLabel")}</Label>
        <Select
          value={state.sourceConnectionId}
          onValueChange={(value) =>
            setState((prev) => ({
              ...prev,
              sourceConnectionId: value,
              sourceRepo: "",
              sourceUrl: "",
            }))
          }
        >
          <SelectTrigger id="ob-connection">
            <SelectValue placeholder={t("connectionPlaceholder")} />
          </SelectTrigger>
          <SelectContent>
            {availableConnections.map((c) => (
              <SelectItem key={c.id} value={c.id}>
                {c.displayName || c.name || c.kind}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {state.sourceConnectionId && (
        <div className="grid gap-2">
          <Label htmlFor="ob-repo">{t("repoLabel")}</Label>
          {reposLoading ? (
            <Skeleton className="h-9 w-full" />
          ) : repoErrorCode ? (
            <p className="text-destructive text-xs">
              {t("repoFetchError", { code: repoErrorCode })}
            </p>
          ) : (
            <Select
              value={state.sourceRepo}
              onValueChange={(value) => {
                const repo = reposList.find((r) => r.fullName === value);
                setState((prev) => ({
                  ...prev,
                  sourceRepo: value,
                  sourceUrl: repo?.webUrl ?? "",
                  sourceDefaultBranch: repo?.defaultBranch || prev.sourceDefaultBranch,
                }));
              }}
            >
              <SelectTrigger id="ob-repo">
                <SelectValue placeholder={t("repoPlaceholder")} />
              </SelectTrigger>
              <SelectContent>
                {reposList.map((r) => (
                  <SelectItem key={r.fullName} value={r.fullName}>
                    {r.fullName}
                    {r.isArchived ? ` (${t("archivedSuffix")})` : ""}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          <p className="text-muted-foreground text-xs">{t("repoHelp")}</p>
        </div>
      )}

      {state.sourceRepo && (
        <div className="grid gap-2">
          <Label htmlFor="ob-default-branch">{t("defaultBranchLabel")}</Label>
          <Input
            id="ob-default-branch"
            value={state.sourceDefaultBranch}
            onChange={(e) =>
              setState((prev) => ({ ...prev, sourceDefaultBranch: e.target.value.trim() }))
            }
          />
        </div>
      )}

      {registersApp(state) && (
        <TemporaryAppFields
          value={{ expiresIn: state.expiresIn, deleteDataOnExpiry: state.deleteDataOnExpiry }}
          onChange={(value) =>
            setState((prev) => ({
              ...prev,
              expiresIn: value.expiresIn ?? "",
              deleteDataOnExpiry: value.deleteDataOnExpiry ?? false,
            }))
          }
          rules={temporary.rules}
          loading={temporary.loading}
          failed={temporary.failed}
          onRetry={temporary.onRetry}
        />
      )}

      <p className="text-muted-foreground text-xs">{t("optionalNote")}</p>
    </div>
  );
}

/**
 * Operators may continue without picking a repo: the submit handler then
 * skips the register-app call. With one picked, the app's lifetime must
 * fit the organization's temporary-app policy, so the policy has to have
 * been read.
 */
export function isRepoValid(
  state: OnboardingWizardState,
  temporary: TemporaryAppPolicyState
): boolean {
  if (!registersApp(state)) return true;
  return !temporary.loading && !temporary.failed && temporaryAppValid(state, temporary.rules);
}
