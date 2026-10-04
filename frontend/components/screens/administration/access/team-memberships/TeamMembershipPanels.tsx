"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { useTranslations } from "next-intl";
import { UsersIcon } from "lucide-react";
import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import type { ListStateController } from "@/components/list/list-state";
import { Button } from "@/components/ui/button";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type {
  ReviewedTeamMembershipFragment,
  GetTeamMembershipReviewQuery,
} from "@/graphql/__generated__/operations";
import { PEOPLE_HREF, TEAMS_HREF } from "../access-nav";

export type MembershipRow = ReviewedTeamMembershipFragment;
export type MembershipReview = NonNullable<
  GetTeamMembershipReviewQuery["astroliftTeamMembershipReview"]
>;
export type AccessSource = MembershipRow["sources"][number];
export type MembershipPhase =
  | "review"
  | "reading"
  | "writing"
  | "uncertain"
  | "committed"
  | "refreshFailed"
  | "refused";

const SOURCE_KEYS: Record<string, "direct" | "inherited" | "idpGroup" | "idpMapping"> = {
  DIRECT: "direct",
  INHERITED: "inherited",
  IDP_GROUP: "idpGroup",
  IDP_MAPPING: "idpMapping",
};

export function MembershipSources({ sources }: { sources: AccessSource[] }) {
  const t = useTranslations("teams.memberships");
  return (
    <ul className="space-y-1 text-sm">
      {sources.map((source) => (
        <li key={source.id} className="min-w-0 [overflow-wrap:anywhere]">
          <span className="text-muted-foreground">
            {SOURCE_KEYS[source.source] ? t(SOURCE_KEYS[source.source]) : source.source}:{" "}
          </span>
          {source.sourceHref?.startsWith("/administration/") ? (
            <Link className="underline" href={source.sourceHref}>
              {source.roleName}
            </Link>
          ) : (
            source.roleName
          )}
          {source.expired ? <span className="text-muted-foreground"> · {t("expired")}</span> : null}
        </li>
      ))}
    </ul>
  );
}

export interface MembershipRosterPanelProps {
  direction: "team" | "person";
  list: ListStateController;
  rows: MembershipRow[];
  totalCount: number;
  loading: boolean;
  error: { message: string } | null;
  onRetry: () => void;
  canAdd: boolean;
  onAdd: () => void;
  onRemove: (row: MembershipRow) => void;
}

/** Pure paged roster. Access provenance is separate from direct attachment. */
export function MembershipRosterPanel(props: MembershipRosterPanelProps) {
  const t = useTranslations("teams.memberships");
  const columns: Column<MembershipRow>[] = [
    {
      id: "principal",
      header: t(props.direction === "team" ? "people" : "teams"),
      cell: (row) => (
        <div className="min-w-0 [overflow-wrap:anywhere]">
          <span>{props.direction === "team" ? row.person.name : row.team.name}</span>
          <div className="text-muted-foreground text-xs">
            {props.direction === "team" ? row.person.email : row.team.slug}
          </div>
        </div>
      ),
    },
    {
      id: "membership",
      header: t("directAttachment"),
      cell: (row) => (
        <span>
          {row.teamMemberId
            ? row.lifecycle === "active"
              ? t("active")
              : row.lifecycle
            : t("noDirectAttachment")}
        </span>
      ),
    },
    {
      id: "sources",
      header: t("sources"),
      cell: (row) => <MembershipSources sources={row.sources} />,
    },
  ];
  return (
    <div className="flex min-w-0 flex-col gap-3">
      {props.canAdd && (
        <div className="flex justify-end">
          <Button onClick={props.onAdd}>
            {t(props.direction === "team" ? "addPeople" : "addTeam")}
          </Button>
        </div>
      )}
      <ListPage
        embedded
        list={props.list}
        label={t(props.direction === "team" ? "people" : "teams")}
        columns={columns}
        rows={props.rows}
        getRowId={(row) => row.person.orgMemberId + ":" + row.team.id}
        rowHref={(row) =>
          props.direction === "team"
            ? `${PEOPLE_HREF}/${row.person.orgMemberId}/teams`
            : `${TEAMS_HREF}/${encodeURIComponent(row.team.slug)}/members`
        }
        loading={props.loading}
        error={props.error}
        onRetry={props.onRetry}
        totalCount={props.totalCount}
        rowActions={(row) =>
          row.canRemove ? (
            <DropdownMenuItem onSelect={() => props.onRemove(row)}>{t("remove")}</DropdownMenuItem>
          ) : null
        }
        empty={{ icon: <UsersIcon className="size-5" />, title: t("empty") }}
      />
      <p className="text-muted-foreground text-sm">{t("sourceReadonly")}</p>
    </div>
  );
}

export interface MembershipReviewPanelProps {
  review: MembershipReview | null;
  phase: MembershipPhase;
  roleId: string;
  onRole: (id: string) => void;
  onConfirm: () => void;
  onRetryOriginal: () => void;
  onReviewAgain: () => void;
  error: string | null;
  roleSelector?: ReactNode;
}

/** Review names the exact direct grants; unknown outcome retains the request. */
export function MembershipReviewPanel({
  review,
  phase,
  roleId,
  onRole,
  onConfirm,
  onRetryOriginal,
  onReviewAgain,
  error,
  roleSelector,
}: MembershipReviewPanelProps) {
  const t = useTranslations("teams.memberships");
  if (phase === "reading") return <p aria-busy="true">{t("loading")}</p>;
  const terminal = phase === "committed" || phase === "refreshFailed";
  if (terminal) return <p role="status">{t(phase)}</p>;
  if (!review)
    return (
      <div role="alert">
        <p>{error ?? t("unavailable")}</p>
        <Button onClick={onReviewAgain}>{t("retry")}</Button>
      </div>
    );
  const add = review.kind === "ADD";
  const direct = review.membership.sources.filter((source) => source.removable);
  const ready =
    phase === "review" &&
    (add ? review.roles.some((role) => role.id === roleId) : review.membership.canRemove);
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <div>
        <p className="font-medium [overflow-wrap:anywhere]">
          {review.membership.person.name} · {review.membership.team.name}
        </p>
        <p className="text-muted-foreground text-sm">
          {add
            ? t("addDescription", { name: review.membership.person.name })
            : t(review.membership.teamMemberId ? "removeDescription" : "removeGrantsDescription", {
                count: direct.length,
              })}
        </p>
      </div>
      {add ? (
        (roleSelector ?? (
          <div className="space-y-2">
            <label htmlFor="reviewed-team-role">{t("role")}</label>
            <Select value={roleId} onValueChange={onRole} disabled={phase !== "review"}>
              <SelectTrigger id="reviewed-team-role">
                <SelectValue placeholder={t("selectRole")} />
              </SelectTrigger>
              <SelectContent>
                {review.roles.map((role) => (
                  <SelectItem key={role.id} value={role.id}>
                    {role.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {review.roles.length === 0 && <p>{t("noGrantableRole")}</p>}
          </div>
        ))
      ) : (
        <MembershipSources sources={direct} />
      )}
      <div>
        <p className="font-medium">{t("remaining")}</p>
        <MembershipSources sources={review.remainingSources} />
        <p className="text-muted-foreground mt-2 text-sm">{t("keptOtherScopes")}</p>
      </div>
      {error && (
        <p role="alert" className="[overflow-wrap:anywhere]">
          {error}
        </p>
      )}
      {phase === "uncertain" ? (
        <div className="space-y-2">
          <p role="status">{t("uncertain")}</p>
          <p className="text-muted-foreground text-sm">{t("requestRecovery")}</p>
          <Button onClick={onRetryOriginal}>{t("retryOriginal")}</Button>
        </div>
      ) : phase === "refused" ? (
        <div>
          <p>{t("refused")}</p>
          <Button onClick={onReviewAgain}>{t("review")}</Button>
        </div>
      ) : (
        <Button disabled={!ready} onClick={onConfirm}>
          {phase === "writing" ? t("loading") : t(add ? "confirmAdd" : "confirmRemove")}
        </Button>
      )}
    </div>
  );
}
