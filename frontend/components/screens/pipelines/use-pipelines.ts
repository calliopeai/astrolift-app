"use client";

import { gql } from "@apollo/client";
import { useMutation, useQuery } from "@apollo/client/react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import type { CursorPage } from "@/components/data-table";
import { useLocalListState } from "@/components/list/use-list-state";
import { GET_ME } from "@/graphql/user/user.queries";
import type { MeQueryData } from "@/graphql/user/user.types";

import {
  narrowPipelines,
  narrowRuns,
  PIPELINE_RUNS_LIST,
  PIPELINES_LIST,
  pipelinesVariables,
  runsVariables,
} from "./pipelines-list";

// ---------------------------------------------------------------------------
// GraphQL
//
// Kept inline, as the rest of this file's operations are. Both fields are
// the cursor-paginated ones: `{ items, nextCursor, totalCount }` out,
// `limit` + `after` (+ `search`) in.
// `$limit: Int` is nullable against the schema's `limit: Int! = 50` because
// that argument carries a default; `$pipelineId: String!` does not, so the
// run stream declares it non-null.
// ---------------------------------------------------------------------------

const LIST_PIPELINES_PAGE = gql`
  query ListPipelinesPage($search: String, $limit: Int, $after: String) {
    astroliftPipelinesPage(search: $search, limit: $limit, after: $after) {
      items {
        id
        name
        repoUrl
        defaultBranch
        tomlPath
        createdAt
      }
      nextCursor
      totalCount
    }
  }
`;

const LIST_PIPELINE_RUNS_PAGE = gql`
  query ListPipelineRunsPage($pipelineId: String!, $search: String, $limit: Int, $after: String) {
    astroliftPipelineRunsPage(
      pipelineId: $pipelineId
      search: $search
      limit: $limit
      after: $after
    ) {
      items {
        id
        runNumber
        triggerKind
        triggerRef
        triggerActor
        status
        startedAt
        finishedAt
      }
      nextCursor
      totalCount
    }
  }
`;

const TRIGGER_PIPELINE_RUN = gql`
  mutation TriggerPipelineRun($input: TriggerPipelineRunInput!) {
    triggerPipelineRun(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
        id
        runNumber
        status
      }
    }
  }
`;

interface TriggerPipelineRunResp {
  triggerPipelineRun: {
    ok: boolean;
    errors: { code: string; message: string }[];
    data: { id: string; runNumber: number; status: string } | null;
  };
}

export type PipelineTab = "pipelines" | "runs";
export const PIPELINE_TABS: readonly PipelineTab[] = ["pipelines", "runs"];

export interface Pipeline {
  id: string;
  name: string;
  repoUrl: string;
  defaultBranch: string;
  tomlPath: string;
  createdAt: string;
}

export interface PipelineRunRow {
  id: string;
  runNumber: number;
  triggerKind: string;
  triggerRef: string;
  triggerActor: string | null;
  status: string;
  startedAt: string | null;
  finishedAt: string | null;
}

interface PipelinesPageResp {
  astroliftPipelinesPage: CursorPage<Pipeline>;
}

interface RunsPageResp {
  astroliftPipelineRunsPage: CursorPage<PipelineRunRow>;
}

/**
 * The data half of PipelinesScreen: the `?tab=` state and the manual
 * trigger (#106, #107). Each tab's table walk has a hook of its own so it
 * runs only while that tab is shown.
 */
export function usePipelines() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const rawTab = searchParams.get("tab") as PipelineTab | null;
  const tab: PipelineTab = rawTab && PIPELINE_TABS.includes(rawTab) ? rawTab : "pipelines";

  function onTabChange(next: PipelineTab) {
    const params = new URLSearchParams(searchParams.toString());
    if (next === "pipelines") params.delete("tab");
    else params.set("tab", next);
    router.replace(`${pathname}${params.size ? `?${params}` : ""}`, { scroll: false });
  }

  const [triggerPipelineRun, { loading: triggering }] =
    useMutation<TriggerPipelineRunResp>(TRIGGER_PIPELINE_RUN);

  async function onTrigger(pipeline: Pipeline) {
    const { data } = await triggerPipelineRun({
      variables: { input: { pipelineId: pipeline.id } },
    });
    if (data?.triggerPipelineRun?.ok) {
      const run = data.triggerPipelineRun.data;
      toast.success(`Run #${run?.runNumber} started`, {
        description: `Pipeline: ${pipeline.name}`,
      });
    } else {
      for (const e of data?.triggerPipelineRun?.errors ?? []) {
        toast.error(`${e.code}: ${e.message}`);
      }
    }
  }

  return { tab, onTabChange, onTrigger, triggering };
}

/** The pipeline list tab (#106): list state in memory, one cursor page. */
export function usePipelineList() {
  // `astroliftPipelinesPage` takes `search`, `limit` and `after` only, no
  // sort argument, so no column declares a `sortKey`.
  const list = useLocalListState(PIPELINES_LIST);
  const query = useQuery<PipelinesPageResp>(LIST_PIPELINES_PAGE, {
    variables: pipelinesVariables(list.filters, list.state),
    fetchPolicy: "cache-and-network",
  });
  const data = query.data ?? query.previousData;
  const page = data?.astroliftPipelinesPage;
  return {
    list,
    rows: narrowPipelines(page?.items ?? [], list.filters),
    totalCount: list.filters.branch || list.filters.owner ? null : (page?.totalCount ?? null),
    nextCursor: page?.nextCursor ?? null,
    loading: query.loading && !data,
    error: query.error && !data ? { message: query.error.message } : null,
    onRetry: () => {
      void query.refetch();
    },
  };
}

/**
 * The run history tab (#107).
 *
 * `astroliftPipelineRunsPage` is single-pipeline by construction:
 * `run_number` is a per-pipeline counter, which is what makes it a valid
 * seek key, so `pipelineId` is a required argument and the tab picks a
 * pipeline first.
 */
export function useRunHistory() {
  const [pipelineId, setPipelineId] = useState<string | null>(null);
  const list = useLocalListState(PIPELINE_RUNS_LIST);
  const me = useQuery<MeQueryData>(GET_ME).data?.me?.profile?.username ?? null;

  const pipelines = useQuery<PipelinesPageResp>(LIST_PIPELINES_PAGE, {
    variables: { limit: 100 },
    fetchPolicy: "cache-and-network",
  });
  const options = pipelines.data?.astroliftPipelinesPage.items ?? [];
  const selected = pipelineId ?? (options.length > 0 ? options[0].id : null);
  const loadingOptions = pipelines.loading && options.length === 0;

  const runs = useQuery<RunsPageResp>(LIST_PIPELINE_RUNS_PAGE, {
    variables: runsVariables(selected, list.filters, list.state),
    fetchPolicy: "cache-and-network",
    skip: !selected,
  });
  const data = runs.data ?? runs.previousData;
  const page = data?.astroliftPipelineRunsPage;
  const narrowed = narrowRuns(page?.items ?? [], list.filters, me);

  return {
    list,
    rows: narrowed.rows,
    totalCount: narrowed.narrowed ? null : (page?.totalCount ?? null),
    nextCursor: page?.nextCursor ?? null,
    loading: Boolean(selected) && runs.loading && !data,
    error: runs.error && !data ? { message: runs.error.message } : null,
    onRetry: () => {
      void runs.refetch();
    },
    options,
    selected,
    onSelect: (id: string) => {
      setPipelineId(id);
      // A new pipeline is a new question: back to its newest runs, filters kept.
      list.setSearch(list.state.q);
    },
    loadingOptions,
  };
}
