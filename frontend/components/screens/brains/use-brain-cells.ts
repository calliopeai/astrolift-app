"use client";

import { useQuery } from "@apollo/client/react";

import type { GetBrainCellsQuery } from "@/graphql/__generated__/operations";
import { GET_BRAIN_CELLS } from "@/graphql/registry/brains.queries";

import type { CellMatrix } from "./substrate-cells";

/**
 * Mimwell's substrate matrix (`astroliftBrainCells`, #2444), read once and
 * kept: the same for every layer. Null until it arrives (or when it fails),
 * and then the forms refuse nothing themselves; the server still checks.
 */
export function useBrainCells({ skip = false }: { skip?: boolean } = {}): CellMatrix | null {
  const { data } = useQuery<GetBrainCellsQuery>(GET_BRAIN_CELLS, {
    skip,
    fetchPolicy: "cache-first",
  });
  return data?.astroliftBrainCells ?? null;
}
