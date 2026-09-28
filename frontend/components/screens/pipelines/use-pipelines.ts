"use client";

import { gql } from "@apollo/client";
import { useMutation, useQuery } from "@apollo/client/react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { useCursorTable, type CursorPage } from "@/components/data-table";

// ---------------------------------------------------------------------------
// GraphQL
//
// Kept inline, as the rest of this file's operations are. Both fields are
// the cursor-paginated ones: `{ items, nextCursor, totalCount }` out,
// `limit` + `after` (+ `search`) in, which is what `useCursorTable` walks.
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

/** The pipeline list tab's table walk (#106). */
export function usePipelineList() {
  // `astroliftPipelinesPage` takes `search`, `limit` and `after` only —
  // no sort argument, so no column declares a `sortKey`. The name / repo /
  // branch comparators this tab used to run reordered one page of a
  // server-ordered catalogue, which is the wrong order at every boundary.
  const table = useCursorTable<Pipeline>({
    query: LIST_PIPELINES_PAGE,
    extract: (d) => (d as PipelinesPageResp | undefined)?.astroliftPipelinesPage,
    searchVariable: "search",
    urlKey: "pipe",
  });
  return { table };
}

/**
 * The run history tab (#107).
 *
 * `astroliftPipelineRunsPage` is single-pipeline by construction —
 * `run_number` is a per-pipeline counter, which is what makes it a valid
 * seek key — so `pipelineId` is a required argument. The cross-pipeline
 * stream this tab used to ask for does not exist server-side: it sent a
 * nullable `$pipelineId: ID` into a `String!` argument, which the server
 * rejects at validation, so the tab has been rendering its empty state
 * unconditionally. It now picks a pipeline instead.
 */
export function useRunHistory() {
  const [pipelineId, setPipelineId] = useState<string | null>(null);

  const pipelines = useQuery<PipelinesPageResp>(LIST_PIPELINES_PAGE, {
    variables: { limit: 100 },
    fetchPolicy: "cache-and-network",
  });
  const options = pipelines.data?.astroliftPipelinesPage.items ?? [];
  const selected = pipelineId ?? (options.length > 0 ? options[0].id : null);
  const loadingOptions = pipelines.loading && options.length === 0;

  const table = useCursorTable<PipelineRunRow>({
    query: LIST_PIPELINE_RUNS_PAGE,
    variables: { pipelineId: selected },
    extract: (d) => (d as RunsPageResp | undefined)?.astroliftPipelineRunsPage,
    searchVariable: "search",
    urlKey: "run",
    skip: !selected,
  });

  return { table, options, selected, onSelect: setPipelineId, loadingOptions };
}
