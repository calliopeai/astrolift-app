"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { useLocalListState } from "@/components/list/use-list-state";
import { useDebounce } from "@/hooks/use-debounce";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import {
  GET_HUGGING_FACE_MODEL,
  SEARCH_HUGGING_FACE_MODELS,
} from "@/graphql/models/catalogue.queries";
import type {
  GetHuggingFaceModelQuery,
  GetHuggingFaceModelQueryVariables,
  SearchHuggingFaceModelsQuery,
  SearchHuggingFaceModelsQueryVariables,
} from "@/graphql/__generated__/operations";
import { hfCatalogueList, hfCatalogueVariables } from "./hf-catalogue-list";
import type { HuggingFaceCataloguePanelProps } from "./HuggingFaceCataloguePanel";

export function useHfCatalogue(
  onPinned: HuggingFaceCataloguePanelProps["onUseRevision"]
): HuggingFaceCataloguePanelProps {
  const t = useTranslations("models.shared.catalogue");
  const { org, loading: orgLoading, error: orgError } = useActiveOrg();
  const organizationId = org?.id ?? "";
  const definition = useMemo(
    () =>
      hfCatalogueList({
        search: t("search"),
        all: t("all"),
        author: t("author"),
        pipelineTag: t("pipelineTag"),
        library: t("library"),
        license: t("license"),
        gating: t("gating"),
        ordering: t("ordering"),
        filterGated: t("filterGated"),
        filterUngated: t("filterUngated"),
        sorts: {
          downloads: t("sort.downloads"),
          likes: t("sort.likes"),
          lastModified: t("sort.lastModified"),
          createdAt: t("sort.createdAt"),
          trendingScore: t("sort.trendingScore"),
        },
      }),
    [t]
  );
  const list = useLocalListState(definition);
  const variables = hfCatalogueVariables(
    list.state.q,
    list.filters,
    list.state.pageSize,
    list.state.after
  );
  const query = useQuery<SearchHuggingFaceModelsQuery, SearchHuggingFaceModelsQueryVariables>(
    SEARCH_HUGGING_FACE_MODELS,
    {
      variables,
      skip: !organizationId || orgLoading || Boolean(orgError),
      fetchPolicy: "cache-and-network",
    }
  );
  const page = query.data?.astroliftHuggingFaceModels;
  const [selection, setSelection] = useState({ organizationId, repoId: "", revision: "" });
  if (selection.organizationId !== organizationId)
    setSelection({ organizationId, repoId: "", revision: "" });
  const selectedRepoId = selection.organizationId === organizationId ? selection.repoId : "";
  const revision = selection.organizationId === organizationId ? selection.revision : "";
  const debouncedRevision = useDebounce(revision, 250);
  const detail = useQuery<GetHuggingFaceModelQuery, GetHuggingFaceModelQueryVariables>(
    GET_HUGGING_FACE_MODEL,
    {
      variables: { repoId: selectedRepoId, revision: debouncedRevision || null },
      skip: !organizationId || !selectedRepoId || orgLoading || Boolean(orgError),
      fetchPolicy: "cache-and-network",
    }
  );
  const detailResult = detail.data?.astroliftHuggingFaceModel;
  const resolving = Boolean(selectedRepoId) && (detail.loading || debouncedRevision !== revision);
  const resolvedModel =
    !resolving &&
    !detail.error &&
    detailResult?.state === "AVAILABLE" &&
    detailResult.model?.repoId === selectedRepoId
      ? detailResult.model
      : null;
  return {
    page: {
      list,
      rows: page?.items ?? [],
      loading: orgLoading || (query.loading && !page),
      stale: query.loading && Boolean(page),
      error: orgError
        ? { message: orgError.message }
        : query.error
          ? { message: query.error.message }
          : !organizationId && !orgLoading
            ? { message: t("unavailable") }
            : null,
      onRetry: () => {
        void query.refetch();
      },
      nextCursor: page?.nextCursor ?? null,
      totalCount: null,
    },
    state: page?.state ?? null,
    source: page?.source ?? null,
    observedAt: page?.observedAt ?? null,
    retryAfterSeconds: page?.retryAfterSeconds ?? null,
    selectedRepoId: selectedRepoId || null,
    revision,
    onSelect: (repoId) => setSelection({ organizationId, repoId, revision: "" }),
    onRevisionChange: (revision) =>
      setSelection({ organizationId, repoId: selectedRepoId, revision }),
    resolving,
    resolvedModel,
    resolutionSource: detailResult?.source ?? null,
    resolutionObservedAt: detailResult?.observedAt ?? null,
    resolutionError:
      detail.error?.message ??
      (detailResult && detailResult.state !== "AVAILABLE" && !resolving
        ? t(
            detailResult.state === "RATE_LIMITED"
              ? "rateLimited"
              : detailResult.state === "UNAVAILABLE"
                ? "unavailable"
                : "noVerifiedRevision"
          )
        : null),
    onRetryResolution: () => {
      void detail.refetch();
    },
    onUseRevision: (model) => {
      if (
        resolvedModel?.repoId === model.repoId &&
        resolvedModel.revisionSha === model.revisionSha &&
        /^[a-f0-9]{40}$/i.test(model.revisionSha)
      )
        onPinned(model);
    },
  };
}
