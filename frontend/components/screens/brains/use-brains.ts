"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { useListState } from "@/components/list/use-list-state";
import { useLocalizedList } from "@/components/list/use-localized-list";
import { useWizardProjects } from "@/components/wizard/use-wizard-projects";
import type {
  HostAppOnBrainMutation,
  HostAppOnBrainMutationVariables,
  IssueBrainReadTokenMutation,
  IssueBrainReadTokenMutationVariables,
  ListBrainsPageQuery,
  ListBrainsPageQueryVariables,
  ListTeamsQuery,
} from "@/graphql/__generated__/operations";
import { LIST_TEAMS } from "@/graphql/identity/identity.queries";
import { HOST_APP_ON_BRAIN, ISSUE_BRAIN_READ_TOKEN } from "@/graphql/registry/brains.mutations";
import { LIST_BRAINS_PAGE } from "@/graphql/registry/brains.queries";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

import type { BrainConnection } from "./brain-connection";
import { BRAINS_LIST, brainsVariables, withTeamOptions } from "./brains-list";
import type { HostBrainAppValues } from "./HostBrainAppDialog";

export type { BrainRow } from "./brains-list";

/**
 * The data half of BrainsScreen: one cursor page of `astroliftBrainsPage`
 * (#2444), searched, filtered by home team and sorted on the server, each
 * row with its cached health and freshness, so no row reads the source
 * host. Connect issues the caller a read token for one layer, held in this
 * state only until the dialog is dismissed. Host an app creates one from the
 * brain-app template and opens it (#2243).
 */
export function useBrains() {
  const t = useTranslations("brains");
  const base = useLocalizedList(BRAINS_LIST);
  const teamRows = useQuery<ListTeamsQuery>(LIST_TEAMS).data?.astroliftTeams;
  const teams = (teamRows ?? []).map((team) => team.slug).join("\n");
  const definition = React.useMemo(
    () => withTeamOptions(base, teams ? teams.split("\n").sort() : []),
    [base, teams]
  );
  const list = useListState(definition);
  const { data, previousData, loading, error, refetch } = useQuery<
    ListBrainsPageQuery,
    ListBrainsPageQueryVariables
  >(LIST_BRAINS_PAGE, {
    variables: brainsVariables(list.filters, list.state),
    fetchPolicy: "cache-and-network",
  });
  const page = (data ?? previousData)?.astroliftBrainsPage;

  const perms = useMyPermissions();
  const [connection, setConnection] = React.useState<BrainConnection | null>(null);
  // Kept out of the Apollo cache, so the token is held only by this state.
  const [issue, issueState] = useMutation<
    IssueBrainReadTokenMutation,
    IssueBrainReadTokenMutationVariables
  >(ISSUE_BRAIN_READ_TOKEN, { fetchPolicy: "no-cache" });
  const router = useRouter();
  const { allProjects } = useWizardProjects();
  const [hostingSlug, setHostingSlug] = React.useState<string | null>(null);
  const [host, hostState] = useMutation<HostAppOnBrainMutation, HostAppOnBrainMutationVariables>(
    HOST_APP_ON_BRAIN
  );
  return {
    list,
    rows: page?.items ?? [],
    totalCount: page?.totalCount ?? null,
    nextCursor: page?.nextCursor ?? null,
    loading: loading && !page,
    stale: loading && !!page,
    error: error && !page ? { message: error.message } : null,
    onRetry: () => {
      void refetch().catch(() => {});
    },
    /** `app.create` somewhere: the server checks the project the wizard picks. */
    canCreate: perms.loading || perms.can("app.create"),
    connection,
    connecting: issueState.loading,
    onConnect: async (slug: string) => {
      const { data: issued } = await issue({ variables: { slug } }).catch(() => ({ data: null }));
      const result = issued?.issueBrainReadToken;
      if (result?.ok && result.data) setConnection(result.data);
      else toast.error(result?.errors[0]?.message ?? t("connectFailed"));
    },
    onDismissConnection: () => setConnection(null),
    hostingSlug,
    hostProjects: allProjects.map((p) => ({ id: p.id, label: `${p.team.slug}/${p.slug}` })),
    hostSubmitting: hostState.loading,
    onHost: (slug: string) => setHostingSlug(slug),
    onDismissHost: () => setHostingSlug(null),
    onSubmitHost: async (values: HostBrainAppValues) => {
      if (!hostingSlug) return;
      const { data: hosted } = await host({
        variables: {
          input: {
            brainSlug: hostingSlug,
            projectId: values.projectId,
            image: values.image,
            name: values.name || null,
            slug: null,
            port: values.port,
          },
        },
      }).catch(() => ({ data: null }));
      const result = hosted?.hostAppOnBrain;
      if (result?.ok && result.data) {
        setHostingSlug(null);
        toast.success(t("hosted", { slug: result.data.app.slug }));
        router.push(`/apps/${result.data.app.slug}`);
      } else toast.error(result?.errors[0]?.message ?? t("hostFailed"));
    },
  };
}

export type BrainsState = ReturnType<typeof useBrains>;
