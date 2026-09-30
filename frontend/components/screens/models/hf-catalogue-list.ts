import type { ListDefinition } from "@/components/list/list-state";
import type { SearchHuggingFaceModelsQueryVariables } from "@/graphql/__generated__/operations";

export const HF_SORTS = [
  "downloads",
  "likes",
  "lastModified",
  "createdAt",
  "trendingScore",
] as const;
export type HfCatalogueCopy = {
  search: string;
  all: string;
  author: string;
  pipelineTag: string;
  library: string;
  license: string;
  gating: string;
  ordering: string;
  filterGated: string;
  filterUngated: string;
  sorts: Record<(typeof HF_SORTS)[number], string>;
};
export function hfCatalogueList(copy: HfCatalogueCopy): ListDefinition {
  return {
    id: "models.huggingFaceCatalogue",
    fields: [
      { key: "author", label: copy.author },
      { key: "pipelineTag", label: copy.pipelineTag },
      { key: "library", label: copy.library },
      { key: "license", label: copy.license },
      {
        key: "gated",
        label: copy.gating,
        options: [
          { value: "true", label: copy.filterGated },
          { value: "false", label: copy.filterUngated },
        ],
      },
      {
        key: "ordering",
        label: copy.ordering,
        options: HF_SORTS.map((value) => ({ value, label: copy.sorts[value] })),
      },
    ],
    searchPlaceholder: copy.search,
    defaultSort: [],
    views: [{ key: "all", label: copy.all, filters: {} }],
    paging: "cursor",
    pageSizes: [10, 20, 30],
    defaultPageSize: 20,
  };
}
export function hfCatalogueVariables(
  search: string,
  filters: Record<string, string>,
  first: number,
  after: string | null
): SearchHuggingFaceModelsQueryVariables {
  return {
    search,
    author: filters.author ?? "",
    pipelineTag: filters.pipelineTag ?? "",
    library: filters.library ?? "",
    license: filters.license ?? "",
    gated: filters.gated === "true" ? true : filters.gated === "false" ? false : null,
    sortBy: filters.ordering ?? "downloads",
    first,
    after,
  };
}
