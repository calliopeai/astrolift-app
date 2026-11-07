import type { CursorTableController } from "@/components/data-table";

export type NvkeSurface = "CONTROLLER" | "TENANT" | "OPERATOR" | "DATASTORE";
export type NvkeStatus =
  | "QUEUED"
  | "RUNNING"
  | "RECONCILING"
  | "AWAITING_READY"
  | "SUCCEEDED"
  | "FAILED"
  | "CANCELLED"
  | "UNCERTAIN";
export interface NvkeConnectionView {
  id: string;
  version: number;
  surface: NvkeSurface;
  endpoint: string;
  isEnabled: boolean;
  hasCredentials: boolean;
  allowHttp: boolean;
  timeoutSeconds: number;
  verifiedAt?: string | null;
  verificationCode: string;
}
export interface NvkeSiteView {
  id: string;
  version: number;
  name: string;
  slug: string;
  organizationNamespace: string;
  isActive: boolean;
  upstreamRevision: string;
  connections: NvkeConnectionView[];
}
export interface NvkeOperationView {
  isInventory: boolean;
  id: string;
  version: number;
  siteId: string;
  requestId: string;
  operationKey: string;
  status: NvkeStatus;
  stage: string;
  generation: number;
  errorCode: string;
  errorMessage: string;
  startedAt?: string | null;
  finishedAt?: string | null;
  cancellationRequestedAt?: string | null;
  deadline: string;
  createdAt: string;
  updatedAt: string;
}
export interface NvkeActionView {
  key: string;
  surface: NvkeSurface;
  verb: string;
  resourceKind: string;
  mutates: boolean;
  permission: string;
  path: string;
}
export interface NvkeAccess {
  read: boolean;
  manage: boolean;
  admin: boolean;
  loading: boolean;
}
export interface NvkeCreateValues {
  name: string;
  slug: string;
  organizationNamespace: string;
}
export interface NvkeConnectionValues {
  surface: NvkeSurface;
  endpoint: string;
  timeoutSeconds: number;
  allowHttp: boolean;
  credentialMode: "preserve" | "replace" | "clear";
  token: string;
  caPem: string;
  clientCertificatePem: string;
  clientKeyPem: string;
}
export interface NvkeRequestValues {
  operationKey: string;
  parametersText: string;
  timeoutSeconds: number;
}
export interface NvkeRecoveryView {
  requestId: string;
  operationKey: string;
  expectedSiteVersion: number;
  timeoutSeconds: number;
}
export interface NvkeSitesScreenProps {
  table: CursorTableController<NvkeSiteView>;
  access: NvkeAccess;
  pending: boolean;
  error: string | null;
  onCreate: (values: NvkeCreateValues) => Promise<boolean>;
}
export interface NvkeSiteScreenProps {
  site: NvkeSiteView | null;
  loading: boolean;
  error: string | null;
  access: NvkeAccess;
  pending: boolean;
  operations: CursorTableController<NvkeOperationView>;
  actions: NvkeActionView[];
  contractRevision: string | null;
  catalogLoading: boolean;
  schemaText: (action: NvkeActionView) => string;
  templateText: (action: NvkeActionView) => string;
  recovery: NvkeRecoveryView | null;
  onRefresh: () => void;
  onConfigure: (values: NvkeConnectionValues) => Promise<boolean>;
  onEnable: (enabled: boolean) => Promise<boolean>;
  onRemove: () => Promise<boolean>;
  onRequest: (values: NvkeRequestValues) => Promise<boolean>;
}
export interface NvkeOperationScreenProps {
  operation: NvkeOperationView | null;
  siteName: string;
  loading: boolean;
  error: string | null;
  pending: boolean;
  canAct: boolean;
  canResume: boolean;
  onRefresh: () => void;
  onCancel: () => Promise<boolean>;
  onResume: () => Promise<boolean>;
  onDownload: () => Promise<void>;
}
export const mayExecute = (access: NvkeAccess, permission: string) =>
  !access.loading &&
  (permission === "nvke.admin"
    ? access.admin
    : permission === "nvke.manage"
      ? access.manage
      : permission === "nvke.read"
        ? access.read
        : false);
export const nvkeFinished = (status: NvkeStatus) =>
  ["SUCCEEDED", "FAILED", "CANCELLED"].includes(status);

// Exactly four interface slots, defined by the pinned integration rather than
// an unbounded inventory. The fixed-cardinality DataTable exception applies.
export const nvkeSurfaces: NvkeSurface[] = ["CONTROLLER", "TENANT", "OPERATOR", "DATASTORE"];
export const fixedNvkeTable = <T>(rows: T[]): CursorTableController<T> => ({
  rows,
  state: rows.length ? "ready" : "empty",
  retry: () => {},
  refetch: () => {},
  totalCount: rows.length,
  pageIndex: 0,
  hasNext: false,
  hasPrev: false,
  next: () => {},
  prev: () => {},
  pageSize: 25,
  setPageSize: () => {},
  search: "",
  setSearch: () => {},
  isStale: false,
  isSearching: false,
  searchEnabled: false,
  sort: undefined,
  toggleSort: () => {},
  sortEnabled: false,
  isFiltered: false,
  clearFilters: () => {},
});
