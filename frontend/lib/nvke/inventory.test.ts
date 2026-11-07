import { expect, it } from "vitest";
import { COLLECTION } from "@/components/screens/nvke/fixtures";
import { nvkeCollectionActions } from "./inventory";

it("offers the collection read first and excludes existing-resource and nested writes", () => {
  const create = COLLECTION.actions.find((a) => a.key === "createCluster")!;
  const actions = nvkeCollectionActions({
    ...COLLECTION,
    actions: [
      create,
      { ...create, key: "createNodePool", path: "/clusters/{clusterID}/node-pools" },
      ...COLLECTION.actions.filter((a) => a.key !== create.key),
    ],
  });
  expect(actions.map((a) => a.key)).toEqual([COLLECTION.key, "createCluster"]);
});
it("does not invent creation for read-only or malformed collections", () => {
  expect(nvkeCollectionActions({ ...COLLECTION, actions: [COLLECTION.actions[0]] })).toEqual([
    COLLECTION.actions[0],
  ]);
  expect(
    nvkeCollectionActions({ ...COLLECTION, actions: COLLECTION.actions.filter((a) => a.mutates) })
  ).toEqual([]);
});
