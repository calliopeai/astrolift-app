"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { useTemporaryAppRules } from "../../apps/use-temporary-app-rules";
import { temporaryAppInput, temporaryAppValid } from "../../apps/temporary-app-model";

import { selectRows } from "@/components/list/select-rows";
import { useListState } from "@/components/list/use-list-state";
import { useLocalizedList } from "@/components/list/use-localized-list";
import { scopedWizardProjects } from "@/components/wizard/use-wizard-projects";
import type {
  HostAppOnBrainMutation,
  HostAppOnBrainMutationVariables,
} from "@/graphql/__generated__/operations";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { LIST_PROJECTS } from "@/graphql/identity/identity.queries";
import type { AstroliftProject } from "@/graphql/identity/identity.types";
import { HOST_APP_ON_BRAIN } from "@/graphql/registry/brains.mutations";

import type { HostBrainAppValues } from "../HostBrainAppDialog";
import { BRAIN_APPS_LIST, BRAIN_APPS_SELECT } from "./brain-detail-lists";
import { hostedAppRows } from "./brain-detail-model";
import type { BrainAppsTabProps } from "./BrainAppsTab";
import type { FramedBrainValue } from "./use-brain-frame";

interface ProjectsResp {
  astroliftProjects: AstroliftProject[];
}

/**
 * The data half of the Apps tab (#2427): the hosted apps (they come with
 * the layer), and Host an app, whose projects are read only once its
 * dialog opens. A hosted app opens next, to be deployed.
 */
export function useBrainApps(framed: FramedBrainValue): BrainAppsTabProps {
  const t = useTranslations("brains");
  const { brain, can } = framed;
  const router = useRouter();
  const all = React.useMemo(() => hostedAppRows(brain.hostedApps ?? []), [brain.hostedApps]);
  const list = useListState(useLocalizedList(BRAIN_APPS_LIST));
  const { state } = list;
  const page = React.useMemo(
    () =>
      selectRows(
        all,
        {
          filters: list.filters,
          q: state.q,
          sort: state.sort,
          page: state.page,
          pageSize: state.pageSize,
        },
        BRAIN_APPS_SELECT
      ),
    [all, list.filters, state.q, state.sort, state.page, state.pageSize]
  );

  const [hosting, setHosting] = React.useState(false);
  const temporary = useTemporaryAppRules({ skip: !hosting });
  const { org } = useActiveOrg();
  const orgId = org?.id ?? "";
  const projects = useQuery<ProjectsResp>(LIST_PROJECTS, {
    skip: !hosting || !orgId,
    fetchPolicy: "cache-and-network",
  });
  const hostProjects = scopedWizardProjects(projects.data?.astroliftProjects ?? [], orgId).map(
    (p) => ({ id: p.id, label: `${p.team.slug}/${p.slug}` })
  );
  const [host, hostState] = useMutation<HostAppOnBrainMutation, HostAppOnBrainMutationVariables>(
    HOST_APP_ON_BRAIN
  );

  return {
    slug: brain.slug,
    served: brain.environments.length > 0,
    canHost: can.host,
    list,
    rows: page.rows,
    totalCount: page.totalCount,
    hosting,
    onHost: () => setHosting(true),
    onDismissHost: () => setHosting(false),
    hostProjects,
    hostSubmitting: hostState.loading,
    temporary,
    onSubmitHost: async (values: HostBrainAppValues) => {
      if (temporary.loading || temporary.failed || !temporaryAppValid(values, temporary.rules))
        return;
      const { data } = await host({
        variables: {
          input: {
            brainSlug: brain.slug,
            projectId: values.projectId,
            image: values.image,
            name: values.name || null,
            slug: null,
            port: values.port,
            ...temporaryAppInput(values),
          },
        },
      }).catch(() => ({ data: null }));
      const result = data?.hostAppOnBrain;
      if (result?.ok && result.data) {
        setHosting(false);
        toast.success(t("hosted", { slug: result.data.app.slug }));
        router.push(`/apps/${result.data.app.slug}`);
      } else toast.error(result?.errors[0]?.message ?? t("hostFailed"));
    },
  };
}
