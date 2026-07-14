"use client";

import { useQuery } from "@apollo/client/react";
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
import { LIST_AVAILABLE_REPOS, LIST_SOURCE_CONNECTIONS } from "@/graphql/scm/scm.queries";
import type { AstroliftRemoteRepoList, AstroliftSourceConnection } from "@/graphql/scm/scm.types";

import type { OnboardingWizardState } from "./types";

interface Props {
  state: OnboardingWizardState;
  setState: React.Dispatch<React.SetStateAction<OnboardingWizardState>>;
}

interface ConnectionsResp {
  astroliftSourceConnections: AstroliftSourceConnection[];
}

interface ReposResp {
  astroliftAvailableRepos: AstroliftRemoteRepoList;
}

/**
 * Step 3 — optional repo picker. When the org has no SCM connection
 * configured yet we render a "skip and add later" CTA rather than a
 * broken empty picker. The wizard treats this step as fully optional
 * — leaving the picker empty registers no app and just creates the
 * team + project.
 */
export function RepoStep({ state, setState }: Props) {
  const t = useTranslations("onboarding.repo");

  const connections = useQuery<ConnectionsResp>(LIST_SOURCE_CONNECTIONS, {
    fetchPolicy: "cache-and-network",
  });

  const availableConnections = React.useMemo(
    () => connections.data?.astroliftSourceConnections ?? [],
    [connections.data]
  );

  const repos = useQuery<ReposResp>(LIST_AVAILABLE_REPOS, {
    variables: { connectionId: state.sourceConnectionId, limit: 100 },
    skip: !state.sourceConnectionId,
    fetchPolicy: "cache-and-network",
  });

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

  if (connections.loading && availableConnections.length === 0) {
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

  const reposList = repos.data?.astroliftAvailableRepos.repos ?? [];
  const repoErrorCode = repos.data?.astroliftAvailableRepos.errorCode ?? null;

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
          {repos.loading ? (
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

      <p className="text-muted-foreground text-xs">{t("optionalNote")}</p>
    </div>
  );
}

/**
 * The repo step is always considered valid — operators may continue
 * without picking a repo. When the picker is left empty, the submit
 * handler simply skips the register-app call.
 */
export function isRepoValid(_state: OnboardingWizardState): boolean {
  return true;
}
