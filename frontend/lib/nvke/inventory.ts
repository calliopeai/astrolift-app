import type { NvkeCollectionView } from "@/components/screens/nvke/types";

export const validNvkeCollectionKey = (value: unknown): value is string =>
  typeof value === "string" && /^[A-Za-z][A-Za-z0-9.]{0,127}$/.test(value);
export const nvkeInventoryHref = (siteId: string, collectionKey: string, operationId?: string) =>
  `/nvke/${siteId}/resources/${encodeURIComponent(collectionKey)}` +
  (operationId ? `?snapshot=${encodeURIComponent(operationId)}` : "");
export const nvkeResourceHref = (
  siteId: string,
  collectionKey: string,
  operationId: string,
  resourceKey: string
) =>
  `/nvke/${siteId}/resources/${encodeURIComponent(collectionKey)}/${operationId}/${encodeURIComponent(resourceKey)}`;

export const nvkeCollectionActions = (collection: NvkeCollectionView) => {
  const read = collection.actions.find(
    (action) => action.key === collection.key && !action.mutates
  );
  if (!read) return [];
  return [
    read,
    ...collection.actions.filter(
      (action) =>
        action.surface === collection.surface &&
        action.mutates &&
        (collection.surface === "DATASTORE"
          ? action.verb === "create"
          : action.verb === "POST" && action.path === read.path)
    ),
  ];
};
