"use client";

import Link from "next/link";
import { useLayoutEffect, useMemo, useState } from "react";
import { useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { PrincipalPage } from "../PrincipalPage";
import { ACCESS_FUNCTIONS, PEOPLE_HREF, TEAMS_HREF } from "../access-nav";
import { personTabs, teamTabs } from "../principal-tabs";
import {
  useListState,
  useLocalListState,
  type ListDefinition,
} from "@/components/list/use-list-state";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import {
  GET_MEMBERSHIP_PERSON,
  GET_MEMBERSHIP_TEAM,
  LIST_REVIEWED_PERSON_TEAMS,
  LIST_REVIEWED_TEAM_MEMBERSHIPS,
  LIST_MEMBERSHIP_TEAMS,
  LIST_TEAM_MEMBER_CANDIDATES,
  LIST_TEAM_MEMBERSHIP_ROLES,
} from "@/graphql/identity/team-memberships.queries";
import type {
  GetMembershipPersonQuery,
  GetMembershipPersonQueryVariables,
  GetMembershipTeamQuery,
  GetMembershipTeamQueryVariables,
  ListReviewedPersonTeamsQuery,
  ListReviewedPersonTeamsQueryVariables,
  ListReviewedTeamMembershipsQuery,
  ListReviewedTeamMembershipsQueryVariables,
  ListTeamMembershipRolesQuery,
  ListTeamMembershipRolesQueryVariables,
  ListMembershipTeamsQuery,
  ListMembershipTeamsQueryVariables,
  ListTeamMemberCandidatesQuery,
  ListTeamMemberCandidatesQueryVariables,
} from "@/graphql/__generated__/operations";
import { MembershipRosterPanel, MembershipReviewPanel } from "./TeamMembershipPanels";
import { MembershipChooserPanel, type MembershipChoice } from "./MembershipChooserPanel";
import { useMembershipAction } from "./use-membership-action";
import { useTeamAccessNavigation } from "./use-team-access-navigation";
import { useTeamMembershipScope } from "./use-team-membership-scope";

type Target = { direction: "team"; slug: string } | { direction: "person"; orgMemberId: string };
export function MembershipRouteClient(props: Target) {
  const scope = useTeamMembershipScope();
  return (
    <MembershipContext
      key={scope.key + JSON.stringify(props)}
      {...props}
      ready={scope.ready}
      scopeError={scope.error}
    />
  );
}

function MembershipContext(props: Target & { ready: boolean; scopeError: string | null }) {
  const t = useTranslations("teams.memberships"),
    detailT = useTranslations("teams.detail");
  const controller = useMemo(() => new AbortController(), []);
  useLayoutEffect(() => () => controller.abort(), [controller]);
  const context = { queryDeduplication: false, fetchOptions: { signal: controller.signal } };
  const teamQuery = useQuery<GetMembershipTeamQuery, GetMembershipTeamQueryVariables>(
    GET_MEMBERSHIP_TEAM,
    {
      variables: { slug: props.direction === "team" ? props.slug : "" },
      skip: !props.ready || props.direction !== "team",
      fetchPolicy: "no-cache",
      context,
    }
  );
  const personQuery = useQuery<GetMembershipPersonQuery, GetMembershipPersonQueryVariables>(
    GET_MEMBERSHIP_PERSON,
    {
      variables: {
        orgMemberId: props.direction === "person" ? props.orgMemberId.toLowerCase() : "",
      },
      skip: !props.ready || props.direction !== "person",
      fetchPolicy: "no-cache",
      context,
    }
  );
  const nav = useTeamAccessNavigation(props.ready);
  const team =
    props.ready && !teamQuery.loading && !teamQuery.error
      ? (teamQuery.data?.astroliftTeamMembershipTeam ?? null)
      : null;
  const person =
    props.ready && !personQuery.loading && !personQuery.error
      ? (personQuery.data?.astroliftTeamMembershipPerson ?? null)
      : null;
  const title =
    team?.name ?? person?.name ?? (props.direction === "team" ? props.slug : props.orgMemberId);
  const base = props.direction === "team" ? team : person;
  const definition = useMemo<ListDefinition>(
    () => ({
      id: `access.reviewed-memberships.${props.direction}`,
      fields: [],
      searchPlaceholder: t(props.direction === "team" ? "searchPeople" : "searchTeams"),
      defaultSort: [],
      views: [{ key: "all", label: t("all"), filters: {} }],
      paging: "numbered",
      pageSizes: [25, 50, 100],
    }),
    [props.direction, t]
  );
  const list = useListState(definition);
  const variables = {
    search: list.state.q || null,
    page: list.state.page,
    pageSize: list.state.pageSize,
  };
  const teamMembers = useQuery<
    ListReviewedTeamMembershipsQuery,
    ListReviewedTeamMembershipsQueryVariables
  >(LIST_REVIEWED_TEAM_MEMBERSHIPS, {
    variables: { ...variables, teamId: team?.id ?? "" },
    skip: !team,
    fetchPolicy: "no-cache",
    context,
  });
  const personTeams = useQuery<ListReviewedPersonTeamsQuery, ListReviewedPersonTeamsQueryVariables>(
    LIST_REVIEWED_PERSON_TEAMS,
    {
      variables: { ...variables, orgMemberId: person?.orgMemberId ?? "" },
      skip: !person,
      fetchPolicy: "no-cache",
      context,
    }
  );
  const query = props.direction === "team" ? teamMembers : personTeams;
  const page =
    props.direction === "team"
      ? teamMembers.data?.astroliftTeamMembershipsPage
      : personTeams.data?.astroliftPersonTeamMembershipsPage;
  const refresh = async () => {
    await Promise.all([
      query.refetch(),
      props.direction === "team" ? teamQuery.refetch() : personQuery.refetch(),
      nav.refetch(),
    ]);
  };
  const action = useMembershipAction(refresh);
  const [choosing, setChoosing] = useState(false);
  const canAdd =
    props.direction === "team"
      ? team?.canManageMembers === true
      : person?.active === true && nav.navigation?.canManageTeamMembers === true;
  const error =
    props.scopeError ??
    teamQuery.error?.message ??
    personQuery.error?.message ??
    nav.error?.message ??
    null;
  const navKeys = {
    people: "canViewPeople",
    teams: "canViewTeams",
    roles: "canViewRoles",
    policies: "canViewPolicies",
    check: "canCheckAccess",
  } as const;
  const permitted = ACCESS_FUNCTIONS.filter((item) => nav.navigation?.[navKeys[item.key]]);
  const translated = (key: string) => detailT(key);
  const tabs =
    props.direction === "team"
      ? teamTabs(props.slug, "members").filter(
          (tab) => tab.key === "members" || nav.navigation?.canViewPeople
        )
      : personTabs(props.orgMemberId, "teams").filter(
          (tab) => tab.key === "teams" || nav.navigation?.canViewPeople
        );
  return (
    <PrincipalPage
      crumbs={[
        {
          label: detailT("admin"),
          switcher: [{ label: detailT("access"), href: "/administration/access", active: true }],
        },
        {
          label: detailT("access"),
          switcher: permitted.map((item) => ({
            label: translated(item.key),
            href: item.href,
            active: item.key === (props.direction === "team" ? "teams" : "people"),
          })),
        },
        {
          label: t(props.direction === "team" ? "teams" : "people"),
          href:
            props.direction === "team"
              ? TEAMS_HREF
              : nav.navigation?.canViewPeople
                ? PEOPLE_HREF
                : TEAMS_HREF,
        },
        { label: title },
      ]}
      presentation={{
        tabsAriaLabel: detailT("principal"),
        loadFailed: t("loadFailed"),
        retry: t("retry"),
      }}
      principal={
        base
          ? {
              kind: props.direction === "team" ? "team" : "user",
              id: team?.id ?? person!.orgMemberId,
              name: title,
              detail: person?.email,
            }
          : null
      }
      fallbackTitle={title}
      context={
        team ? (
          <span className="font-mono">{team.slug}</span>
        ) : person ? (
          <span>{t(person.active ? "active" : "inactive")}</span>
        ) : undefined
      }
      tabs={tabs.map((tab) => ({
        ...tab,
        label: tab.key === "teams" ? t("teams") : detailT(tab.key),
      }))}
      loading={!props.ready || teamQuery.loading || personQuery.loading}
      error={error ? { message: error } : null}
      onRetry={() => {
        void (props.direction === "team" ? teamQuery.refetch() : personQuery.refetch()).catch(
          () => {}
        );
      }}
      notFound={props.ready && !teamQuery.loading && !personQuery.loading && !error && !base}
      notFoundCopy={{
        title: t("unavailable"),
        description: t("loadFailed"),
        backHref: TEAMS_HREF,
        backLabel: t("teams"),
      }}
    >
      {props.direction === "team" && team?.canManageMembers && nav.navigation?.canViewPeople && (
        <div className="flex justify-end">
          <Button variant="outline" asChild>
            <Link href={`${TEAMS_HREF}/${encodeURIComponent(props.slug)}/assign-roles`}>
              {t("bulkAssign")}
            </Link>
          </Button>
        </div>
      )}
      <MembershipRosterPanel
        direction={props.direction}
        list={list}
        rows={!query.loading && !query.error ? (page?.items ?? []) : []}
        totalCount={!query.loading && !query.error ? (page?.totalCount ?? 0) : 0}
        loading={query.loading}
        error={
          query.error
            ? { message: query.error.message }
            : !query.loading && !page
              ? { message: t("unavailable") }
              : null
        }
        onRetry={() => {
          void query.refetch().catch(() => {});
        }}
        canAdd={canAdd && !choosing && !action.target}
        onAdd={() => setChoosing(true)}
        onRemove={(row) => action.openRemove(row)}
      />
      <Dialog
        open={choosing || Boolean(action.target)}
        onOpenChange={(open) => {
          if (!open && action.canClose) {
            setChoosing(false);
            action.close();
          }
        }}
      >
        <DialogContent
          className="max-h-[90vh] overflow-y-auto sm:max-w-2xl"
          hideCloseButton={!action.canClose}
        >
          <DialogHeader>
            <DialogTitle>
              {action.target
                ? t("review")
                : t(props.direction === "team" ? "addPeople" : "addTeam")}
            </DialogTitle>
            <DialogDescription>{t("keptOtherScopes")}</DialogDescription>
          </DialogHeader>
          {action.target ? (
            <MembershipReviewPanel
              {...action}
              roleSelector={
                action.target.kind === "ADD" && action.phase === "review" ? (
                  <MembershipRolePickerClient
                    teamId={action.target.teamId}
                    roleId={action.roleId}
                    roleName={
                      action.review?.roles.find((role) => role.id === action.roleId)?.name ?? null
                    }
                    onChoose={action.onRole}
                  />
                ) : undefined
              }
            />
          ) : choosing && base ? (
            <MembershipChoiceClient
              direction={props.direction}
              teamId={team?.id ?? null}
              orgMemberId={person?.orgMemberId ?? null}
              onChoose={(choice) => {
                setChoosing(false);
                action.openAdd(
                  props.direction === "team" ? team!.id : choice.id,
                  props.direction === "person" ? person!.orgMemberId : choice.id
                );
              }}
            />
          ) : null}
          {action.canClose && (
            <Button
              variant="outline"
              onClick={() => {
                setChoosing(false);
                action.close();
              }}
            >
              {t("cancel")}
            </Button>
          )}
        </DialogContent>
      </Dialog>
    </PrincipalPage>
  );
}

function MembershipChoiceClient({
  direction,
  teamId,
  onChoose,
}: {
  direction: "team" | "person";
  teamId: string | null;
  orgMemberId: string | null;
  onChoose: (choice: MembershipChoice) => void;
}) {
  const t = useTranslations("teams.memberships");
  const definition = useMemo<ListDefinition>(
    () => ({
      id: `access.membership-choices.${direction}`,
      fields: [],
      searchPlaceholder: t(direction === "team" ? "searchPeople" : "searchTeams"),
      defaultSort: [],
      views: [{ key: "all", label: t("all"), filters: {} }],
      paging: "numbered",
      pageSizes: [25, 50, 100],
    }),
    [direction, t]
  );
  const list = useLocalListState(definition),
    abort = useMemo(() => new AbortController(), []);
  useLayoutEffect(() => () => abort.abort(), [abort]);
  const variables = {
    search: list.state.q || null,
    page: list.state.page,
    pageSize: list.state.pageSize,
  };
  const context = { queryDeduplication: false, fetchOptions: { signal: abort.signal } };
  const people = useQuery<ListTeamMemberCandidatesQuery, ListTeamMemberCandidatesQueryVariables>(
    LIST_TEAM_MEMBER_CANDIDATES,
    {
      variables: { ...variables, teamId: teamId ?? "" },
      skip: direction !== "team" || !teamId,
      fetchPolicy: "no-cache",
      context,
    }
  );
  const teams = useQuery<ListMembershipTeamsQuery, ListMembershipTeamsQueryVariables>(
    LIST_MEMBERSHIP_TEAMS,
    { variables, skip: direction !== "person", fetchPolicy: "no-cache", context }
  );
  const page =
    direction === "team"
      ? people.data?.astroliftTeamMemberCandidatesPage
      : teams.data?.astroliftMembershipTeamsPage;
  const query = direction === "team" ? people : teams;
  const rows =
    direction === "team"
      ? (people.data?.astroliftTeamMemberCandidatesPage.items.map((person) => ({
          id: person.orgMemberId,
          name: person.name,
          detail: person.email,
        })) ?? [])
      : (teams.data?.astroliftMembershipTeamsPage.items.map((team) => ({
          id: team.id,
          name: team.name,
          detail: team.slug,
        })) ?? []);
  return (
    <MembershipChooserPanel
      direction={direction}
      list={list}
      rows={!query.loading && !query.error ? rows : []}
      totalCount={!query.loading && !query.error ? (page?.totalCount ?? 0) : 0}
      loading={query.loading}
      error={
        query.error
          ? { message: query.error.message }
          : !query.loading && !page
            ? { message: t("unavailable") }
            : null
      }
      onRetry={() => {
        void query.refetch().catch(() => {});
      }}
      onChoose={onChoose}
    />
  );
}

function MembershipRolePickerClient({
  teamId,
  roleId,
  roleName,
  onChoose,
}: {
  teamId: string;
  roleId: string;
  roleName: string | null;
  onChoose: (id: string) => void;
}) {
  const t = useTranslations("teams.memberships");
  const definition = useMemo<ListDefinition>(
    () => ({
      id: "access.membership-role-choices",
      fields: [],
      searchPlaceholder: t("searchRoles"),
      defaultSort: [],
      views: [{ key: "all", label: t("all"), filters: {} }],
      paging: "numbered",
      pageSizes: [25, 50, 100],
    }),
    [t]
  );
  const list = useLocalListState(definition),
    abort = useMemo(() => new AbortController(), []);
  useLayoutEffect(() => () => abort.abort(), [abort]);
  const query = useQuery<ListTeamMembershipRolesQuery, ListTeamMembershipRolesQueryVariables>(
    LIST_TEAM_MEMBERSHIP_ROLES,
    {
      variables: {
        teamId,
        search: list.state.q || null,
        page: list.state.page,
        pageSize: list.state.pageSize,
      },
      fetchPolicy: "no-cache",
      context: { queryDeduplication: false, fetchOptions: { signal: abort.signal } },
    }
  );
  const page = query.data?.astroliftTeamMembershipRolesPage;
  return (
    <div className="space-y-2">
      <p className="font-medium">
        {t("role")}: {roleId && roleName ? roleName : t("selectRole")}
      </p>
      <MembershipChooserPanel
        direction="person"
        kind="role"
        list={list}
        rows={
          !query.loading && !query.error
            ? (page?.items.map((role) => ({ id: role.id, name: role.name, detail: "" })) ?? [])
            : []
        }
        totalCount={!query.loading && !query.error ? (page?.totalCount ?? 0) : 0}
        loading={query.loading}
        error={
          query.error
            ? { message: query.error.message }
            : !query.loading && !page
              ? { message: t("unavailable") }
              : null
        }
        onRetry={() => {
          void query.refetch().catch(() => {});
        }}
        onChoose={(choice) => onChoose(choice.id)}
      />
    </div>
  );
}
