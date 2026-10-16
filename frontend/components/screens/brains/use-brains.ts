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
  ListBrainsQuery,
} from "@/graphql/__generated__/operations";
import { HOST_APP_ON_BRAIN, ISSUE_BRAIN_READ_TOKEN } from "@/graphql/registry/brains.mutations";
import { LIST_BRAINS } from "@/graphql/registry/brains.queries";

import type { BrainConnection } from "./brain-connection";
import { BRAINS_LIST } from "./brains-list";
import type { HostBrainAppValues } from "./HostBrainAppDialog";

export type BrainRow = ListBrainsQuery["astroliftBrains"][number];

/**
 * The data half of BrainsScreen: the server's search over readable brains,
 * and Connect, which issues the caller a read token for one of them. The
 * token lives in this state only until the dialog is dismissed. Host an app
 * creates one from the brain-app template and opens it (#2243).
 */
export function useBrains() {
  const t = useTranslations("brains");
  const list = useListState(useLocalizedList(BRAINS_LIST));
  const search = list.state.q.trim();
  const { data, previousData, loading, error, refetch } = useQuery<ListBrainsQuery>(LIST_BRAINS, {
    variables: { search: search || null },
  });
  const rows = (data ?? previousData)?.astroliftBrains ?? [];
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
    rows,
    totalCount: data ? rows.length : null,
    loading: loading && !data && !previousData,
    stale: loading && !!previousData,
    error: error ? { message: error.message } : null,
    onRetry: () => {
      void refetch().catch(() => {});
    },
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
