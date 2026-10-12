"use client";

import { GitBranchIcon, LockIcon, UnlockIcon } from "lucide-react";
import { useFormatter, useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { ScmReauthAction } from "@/components/ScmConnectPrompt";
import type { UseScmConnect } from "@/components/use-scm-connect";
import { Badge } from "@/components/ui/badge";
import {
  Combobox,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxInput,
  ComboboxItem,
  ComboboxList,
} from "@/components/ui/combobox";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import type {
  AstroliftRemoteRepoList,
  AstroliftSourceConnection,
  ScmConnectionKind,
} from "@/graphql/scm/scm.types";

export interface SourceRepositoryPickerProps {
  connections: AstroliftSourceConnection[];
  connectionId: string;
  sourceRepo: string;
  onConnectionChange: (id: string) => void;
  reposLoading: boolean;
  repoList: AstroliftRemoteRepoList | undefined;
  search: string;
  onSearchChange: (search: string) => void;
  onPickRepo: (fullName: string) => void;
  repoCounts: Map<string, number>;
  registeredKind: "app" | "agent";
  registeredNote: (count: number) => string;
  scm: UseScmConnect;
  missingHostLink: ReactNode;
  emptyReposLink: ReactNode;
  pickedDetails: ReactNode;
}

/** Shared selection UI; callers retain their cluster gate, scan ref and side effects. */
export function SourceRepositoryPicker({
  connections: usable,
  connectionId,
  sourceRepo,
  onConnectionChange,
  reposLoading,
  repoList,
  search,
  onSearchChange,
  onPickRepo,
  repoCounts,
  registeredKind,
  registeredNote,
  scm,
  missingHostLink,
  emptyReposLink,
  pickedDetails,
}: SourceRepositoryPickerProps) {
  const t = useTranslations("repoPicker");
  const format = useFormatter();
  const pickedConnection = usable.find((connection) => connection.id === connectionId);
  return (
    <>
      <div className="space-y-2">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <div className="flex items-center gap-2">
            <Label htmlFor="connection">{t("connection")}</Label>
            {pickedConnection && (
              <Badge variant="outline" className="text-2xs font-normal">
                {connectionKindLabel(pickedConnection.kind as ScmConnectionKind)}
              </Badge>
            )}
          </div>
          {missingHostLink}
        </div>
        <Select value={connectionId} onValueChange={(v) => onConnectionChange(v)}>
          <SelectTrigger id="connection">
            <SelectValue placeholder={t("chooseConnection")} />
          </SelectTrigger>
          <SelectContent>
            {usable.map((c) => (
              <SelectItem key={c.id} value={c.id}>
                <span className="font-medium">{c.displayName || c.name}</span>{" "}
                <span className="text-muted-foreground text-xs">
                  ({connectionKindLabel(c.kind as ScmConnectionKind)})
                </span>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {connectionId && (
        <div className="space-y-2">
          <Label htmlFor="repo-picker">{t("repository")}</Label>
          {reposLoading ? (
            <Skeleton className="h-10 w-full" />
          ) : repoList?.errorCode ? (
            <div className="text-destructive flex flex-col items-start gap-1 text-xs">
              <span>{repoList.errorMessage ?? repoList.errorCode}</span>
              {repoList.recoverable && (
                <ScmReauthAction connectionKind={pickedConnection?.kind ?? ""} scm={scm} />
              )}
            </div>
          ) : repoList && repoList.repos.length > 0 ? (
            <>
              <Combobox
                items={repoList.repos}
                itemToStringLabel={(r) => (r as { fullName: string }).fullName}
                value={
                  sourceRepo
                    ? (repoList.repos.find((r) => r.fullName === sourceRepo) ?? null)
                    : null
                }
                onValueChange={(v) => {
                  if (v && typeof v === "object" && "fullName" in v) {
                    onPickRepo((v as { fullName: string }).fullName);
                  }
                }}
                inputValue={search}
                onInputValueChange={(v) => onSearchChange(v ?? "")}
              >
                <ComboboxInput placeholder={t("search", { count: repoList.repos.length })} />
                <ComboboxContent>
                  <ComboboxEmpty>{t("noMatches")}</ComboboxEmpty>
                  <ComboboxList>
                    {(item) => {
                      const r = item as {
                        fullName: string;
                        name: string;
                        visibility: string;
                        defaultBranch: string | null;
                        isFork: boolean;
                        isArchived: boolean;
                        pushedAt: string | null;
                      };
                      const existingCount = repoCounts.get(r.fullName) ?? 0;
                      return (
                        <ComboboxItem key={r.fullName} value={r}>
                          <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                            <div className="flex min-w-0 items-center gap-2">
                              <span className="truncate font-mono text-xs">{r.fullName}</span>
                              {existingCount > 0 && (
                                <Badge
                                  variant="secondary"
                                  className="text-2xs shrink-0 gap-1 px-1 py-0"
                                  title={registeredNote(existingCount)}
                                >
                                  {t("alreadyRegistered", {
                                    count: existingCount,
                                    kind: registeredKind,
                                  })}
                                </Badge>
                              )}
                            </div>
                            <div className="text-muted-foreground text-2xs flex items-center gap-2">
                              <VisibilityBadge visibility={r.visibility} />
                              {r.defaultBranch && (
                                <span className="inline-flex items-center gap-1">
                                  <GitBranchIcon className="size-3" />
                                  {r.defaultBranch}
                                </span>
                              )}
                              {r.isFork && <span>{t("fork")}</span>}
                              {r.isArchived && <span>{t("archived")}</span>}
                              {r.pushedAt && (
                                <span>
                                  {t("updated", {
                                    when: format.relativeTime(new Date(r.pushedAt), new Date()),
                                  })}
                                </span>
                              )}
                            </div>
                          </div>
                        </ComboboxItem>
                      );
                    }}
                  </ComboboxList>
                </ComboboxContent>
              </Combobox>
              {pickedDetails}
            </>
          ) : (
            <p className="text-muted-foreground text-xs">
              {t.rich("noRepos", { link: () => emptyReposLink })}
            </p>
          )}
        </div>
      )}
    </>
  );
}

function connectionKindLabel(kind: ScmConnectionKind): string {
  switch (kind) {
    case "github_app_install":
      return "GitHub App";
    case "github_oauth_user":
      return "GitHub OAuth";
    case "github_pat":
      return "GitHub PAT";
    case "gitlab_oauth_user":
      return "GitLab OAuth";
    case "gitlab_pat":
      return "GitLab PAT";
    case "bitbucket_oauth_user":
      return "Bitbucket OAuth";
    case "bitbucket_pat":
      return "Bitbucket PAT";
    case "gitea_oauth_user":
      return "Gitea OAuth";
    case "gitea_pat":
      return "Gitea PAT";
    default:
      return kind;
  }
}

function VisibilityBadge({ visibility }: { visibility: string }) {
  if (visibility === "private" || visibility === "internal") {
    return (
      <Badge variant="secondary" className="text-2xs gap-1 px-1 py-0">
        <LockIcon className="size-2.5" /> {visibility}
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className="text-2xs gap-1 px-1 py-0">
      <UnlockIcon className="size-2.5" /> {visibility}
    </Badge>
  );
}
