"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { useRouter, useSearchParams } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { useTemporaryAppRules } from "../apps/use-temporary-app-rules";
import {
  temporaryAppInput,
  temporaryAppValid,
  type TemporaryAppPolicyState,
} from "../apps/temporary-app-model";

import { parseListState, useListState } from "@/components/list/use-list-state";
import { useLocalizedList } from "@/components/list/use-localized-list";
import { selectRows } from "@/components/list/select-rows";
import { useWizardProjects } from "@/components/wizard/use-wizard-projects";
import type {
  HostAppOnBrainMutation,
  HostAppOnBrainMutationVariables,
  IssueBrainReadTokenMutation,
  IssueBrainReadTokenMutationVariables,
  ListBrainsQuery,
} from "@/graphql/__generated__/operations";
import { HOST_APP_ON_BRAIN, ISSUE_BRAIN_READ_TOKEN } from "@/graphql/registry/brains.mutations";
import { LIST_BRAINS } from "@/graphql/registry/brains.queries";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

import type { BrainConnection } from "./brain-connection";
import { BRAINS_LIST, BRAINS_SELECT, brainTeams, withTeamOptions } from "./brains-list";
import type { HostBrainAppValues } from "./HostBrainAppDialog";

export type { BrainRow } from "./brains-list";

/**
 * The data half of BrainsScreen: the server's search over readable brains,
 * filtered by team, sorted and paged from the rows in hand (the field takes
 * no team, sort or page argument). Connect issues the caller a read token
 * for one layer, held in this state only until the dialog is dismissed.
 * Host an app creates one from the brain-app template and opens it (#2243).
 */
export function useBrains() {
  const t = useTranslations("brains");
  const base = useLocalizedList(BRAINS_LIST);
  // The search reaches the server, so it is read before the team options,
  // which come from the rows the search returns.
  const params = useSearchParams();
  const search = parseListState(base, params?.toString() ?? "").q.trim();
  const { data, previousData, loading, error, refetch } = useQuery<ListBrainsQuery>(LIST_BRAINS, {
    variables: { search: search || null },
    fetchPolicy: "cache-and-network",
  });
  const all = (data ?? previousData)?.astroliftBrains;
  const teams = brainTeams(all ?? []).join("\n");
  const definition = React.useMemo(
    () => withTeamOptions(base, teams ? teams.split("\n") : []),
    [base, teams]
  );
  const list = useListState(definition);
  const { state } = list;
  const page = React.useMemo(
    () =>
      selectRows(
        all ?? [],
        {
          filters: list.filters,
          q: state.q,
          sort: state.sort,
          page: state.page,
          pageSize: state.pageSize,
        },
        BRAINS_SELECT
      ),
    [all, list.filters, state.q, state.sort, state.page, state.pageSize]
  );

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
  const temporary = useTemporaryAppRules({ skip: !hostingSlug });
  const [host, hostState] = useMutation<HostAppOnBrainMutation, HostAppOnBrainMutationVariables>(
    HOST_APP_ON_BRAIN
  );
  return {
    list,
    rows: page.rows,
    totalCount: all ? page.totalCount : null,
    loading: loading && !all,
    stale: loading && !!all,
    error: error && !all ? { message: error.message } : null,
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
    temporary,
    onHost: (slug: string) => setHostingSlug(slug),
    onDismissHost: () => setHostingSlug(null),
    onSubmitHost: async (values: HostBrainAppValues) => {
      if (
        !hostingSlug ||
        temporary.loading ||
        temporary.failed ||
        !temporaryAppValid(values, temporary.rules)
      )
        return;
      const { data: hosted } = await host({
        variables: {
          input: {
            brainSlug: hostingSlug,
            projectId: values.projectId,
            image: values.image,
            name: values.name || null,
            slug: null,
            port: values.port,
            ...temporaryAppInput(values),
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

export type BrainsState = Omit<ReturnType<typeof useBrains>, "temporary"> & {
  temporary?: TemporaryAppPolicyState;
};
