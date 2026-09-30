import type { ReactNode } from "react";
import type { AstroliftModelPromptReadinessState } from "@/graphql/__generated__/operations";
import type { BatchResult, SavedMessage, SavedSessionIndexEntry } from "./saved-sessions";

export type PromptError =
  | "refused"
  | "unavailable"
  | "timedOut"
  | "failed"
  | "rateLimited"
  | "conflict"
  | "invalid"
  | "transport";
export type ReadinessState =
  | AstroliftModelPromptReadinessState
  | "loading"
  | "refused"
  | "transport"
  | "missing"
  | "unselected";
export type EndpointOption = {
  id: string;
  name: string;
  variant: string;
  registeredAppSlug?: string | null;
  environmentName?: string | null;
};
export type PromptOutcome = {
  ok: boolean;
  reply: string;
  error?: PromptError;
  latencyMs?: number | null;
  totalTokens?: number | null;
};
export type PromptExecutor = (prompt: string) => Promise<PromptOutcome>;
export type PlaygroundScreenProps = {
  tab: "chat" | "batch";
  setTab: (tab: "chat" | "batch") => void;
  title: string;
  setTitle: (title: string) => void;
  messages: SavedMessage[];
  prompt: string;
  setPrompt: (prompt: string) => void;
  model: string;
  setModel: (id: string) => void;
  modelName: string;
  models: EndpointOption[];
  search: string;
  setSearch: (value: string) => void;
  page: number;
  totalCount: number;
  setPage: (page: number) => void;
  catalogLoading: boolean;
  catalogError: boolean;
  onCatalogRetry: () => void;
  readiness: ReadinessState;
  onReadinessRetry: () => void;
  maxPromptChars: number;
  maxOutputTokens: number;
  maxWaitSeconds: number;
  canSend: boolean;
  loading: boolean;
  error: PromptError | null;
  savedLoading?: boolean;
  savedSessions: SavedSessionIndexEntry[];
  activeSavedId: string | null;
  onSend: () => void;
  onSave: () => void;
  onShare: () => Promise<void>;
  onLoad: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
  onStar: (id: string) => void;
  batch: ReactNode;
};
export type PlaygroundBatchProps = {
  input: string;
  setInput: (value: string) => void;
  inputs: string[];
  running: boolean;
  cancelled: boolean;
  canRun: boolean;
  invalid: boolean;
  results: BatchResult[];
  onRun: () => Promise<void>;
  onCancel: () => void;
  onExportCsv: () => void;
  onExportJsonl: () => void;
  onCopyJson: () => Promise<void>;
};
