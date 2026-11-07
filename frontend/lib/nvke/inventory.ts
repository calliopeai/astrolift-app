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
