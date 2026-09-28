"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { toast } from "sonner";

import { useCursorTable } from "@/components/data-table";
import {
  DELETE_SSH_DEPLOY_KEY,
  DISCONNECT_SOURCE,
  GENERATE_SSH_DEPLOY_KEY,
  ROTATE_WEBHOOK_SECRET,
  UPDATE_SOURCE_CONNECTION,
} from "@/graphql/scm/scm.mutations";
import {
  LIST_SOURCE_CONNECTIONS,
  LIST_SOURCE_CONNECTIONS_PAGE,
  LIST_SSH_DEPLOY_KEYS,
  LIST_SSH_DEPLOY_KEYS_PAGE,
} from "@/graphql/scm/scm.queries";
import type {
  AstroliftSourceConnection,
  AstroliftSshDeployKey,
  AstroliftSshDeployKeyCreated,
  AstroliftWebhookSecretReveal,
  MutationResult,
} from "@/graphql/scm/scm.types";

interface ConnectionsResp {
  astroliftSourceConnections: AstroliftSourceConnection[];
}

interface ConnectionsPageResp {
  astroliftSourceConnectionsPage: {
    items: AstroliftSourceConnection[];
    nextCursor?: string | null;
    totalCount?: number | null;
  };
}

interface KeysPageResp {
  astroliftSshDeployKeysPage: {
    items: AstroliftSshDeployKey[];
    nextCursor?: string | null;
    totalCount?: number | null;
  };
}

// Refetched by operation NAME, not by document: the cursor, page size and
// search term live inside the controller, so no literal variables object
// names the page the operator is actually looking at.
const REFETCH_CONNECTIONS = "ListSourceConnectionsPage";
const REFETCH_KEYS = "ListSshDeployKeysPage";

/**
 * The connection and deploy-key walks plus the row mutations behind the
 * Source providers panel. The data half of SourceProvidersScreen.
 *
 * The confirm handlers throw on failure: ConfirmDialog keeps itself open
 * and toasts the message.
 */
export function useSourceProviders() {
  // OAuth-callback outcome toasts moved to a layout-level component
  // (#759, `components/ScmCallbackToast`) so they surface regardless
  // of where `return_to` lands. Refetching the list when we land on
  // this page with a success param is now the only page-specific
  // bit; the toast handler in the layout consumes the param before
  // we run, so we can't read it directly. The queries already
  // cache-and-network-fetch on mount, so the row state is up to
  // date without an explicit refetch hook here.
  const connTable = useCursorTable<AstroliftSourceConnection>({
    query: LIST_SOURCE_CONNECTIONS_PAGE,
    extract: (d) => (d as ConnectionsPageResp | undefined)?.astroliftSourceConnectionsPage,
    searchVariable: "search",
    urlKey: "conn",
  });

  const keyTable = useCursorTable<AstroliftSshDeployKey>({
    query: LIST_SSH_DEPLOY_KEYS_PAGE,
    // `null` keeps the list field's meaning: every key in the org,
    // org-scoped and per-app alike. `""` would narrow to org-scoped only.
    variables: { appSlug: null },
    extract: (d) => (d as KeysPageResp | undefined)?.astroliftSshDeployKeysPage,
    searchVariable: "search",
    urlKey: "key",
  });

  // Org-wide connection list, kept for the "needs a Client ID" callout
  // — NOT for table rows. The callout enumerates every incomplete
  // GitHub App connection in the org; sourcing it from the table's page
  // would silently scope the prompt to whichever 25 rows are on screen.
  // It is the same document `/providers` already preloads and the repo
  // pickers already watch, so on this route it costs nothing extra.
  const conns = useQuery<ConnectionsResp>(LIST_SOURCE_CONNECTIONS);

  const [disconnect, disconnectState] = useMutation<{
    disconnectSource: MutationResult<{ id: string }>;
  }>(DISCONNECT_SOURCE, {
    refetchQueries: [{ query: LIST_SOURCE_CONNECTIONS }, REFETCH_CONNECTIONS],
    awaitRefetchQueries: true,
  });

  const [deleteKey, deleteKeyState] = useMutation<{
    deleteSshDeployKey: MutationResult<{ id: string }>;
  }>(DELETE_SSH_DEPLOY_KEY, {
    refetchQueries: [REFETCH_KEYS],
    awaitRefetchQueries: true,
  });

  const [rotateSecret, rotateSecretState] = useMutation<{
    rotateWebhookSecret: MutationResult<AstroliftWebhookSecretReveal>;
  }>(ROTATE_WEBHOOK_SECRET, {
    refetchQueries: [{ query: LIST_SOURCE_CONNECTIONS }, REFETCH_CONNECTIONS],
    awaitRefetchQueries: true,
  });

  /** Resolves with the one-time secret reveal; throws on failure. */
  async function handleRotateSecret(
    c: AstroliftSourceConnection
  ): Promise<AstroliftWebhookSecretReveal> {
    const { data } = await rotateSecret({
      variables: { input: { connectionId: c.id } },
    });
    if (data?.rotateWebhookSecret.ok && data.rotateWebhookSecret.data) {
      return data.rotateWebhookSecret.data;
    }
    throw new Error(data?.rotateWebhookSecret.errors?.[0]?.message ?? "Rotation failed");
  }

  async function handleDisconnect(c: AstroliftSourceConnection) {
    const { data } = await disconnect({ variables: { input: { id: c.id } } });
    if (data?.disconnectSource.ok) toast.success(`Disconnected ${c.name}`);
    else throw new Error(data?.disconnectSource.errors?.[0]?.message ?? "Disconnect failed");
  }

  async function handleDeleteKey(k: AstroliftSshDeployKey) {
    const { data } = await deleteKey({ variables: { input: { id: k.id } } });
    if (data?.deleteSshDeployKey.ok) toast.success(`Deleted ${k.name}`);
    else throw new Error(data?.deleteSshDeployKey.errors?.[0]?.message ?? "Delete failed");
  }

  return {
    connTable,
    keyTable,
    incompleteClientIdConnections: (conns.data?.astroliftSourceConnections ?? []).filter(
      (c) => c.needsClientId
    ),
    disconnecting: disconnectState.loading,
    deletingKey: deleteKeyState.loading,
    rotatingSecret: rotateSecretState.loading,
    disconnect: handleDisconnect,
    deleteKey: handleDeleteKey,
    rotateSecret: handleRotateSecret,
    // The connect / generate dialogs live in their own files and still
    // refetch the deprecated flat lists, which no longer feed these tables.
    // Refreshing on close is the in-place fix: a dialog that closes after a
    // successful mutation is exactly when the page needs new rows.
    refreshConnections: connTable.refetch,
    refreshKeys: keyTable.refetch,
  };
}

export type SourceProvidersData = ReturnType<typeof useSourceProviders>;

interface UpdateResp {
  updateSourceConnection: MutationResult<{
    id: string;
    appClientId: string;
    needsClientId: boolean;
  }>;
}

/**
 * The UpdateSourceConnection mutation behind the Add Client ID sheet
 * (#525 recovery flow). The data half of AddClientIdSheet.
 */
export function useAddClientId() {
  const [update, { loading }] = useMutation<UpdateResp>(UPDATE_SOURCE_CONNECTION, {
    refetchQueries: [{ query: LIST_SOURCE_CONNECTIONS }],
    awaitRefetchQueries: true,
  });

  /** Resolves true when the Client ID saved; the sheet then closes. */
  async function save(connection: AstroliftSourceConnection, appClientId: string) {
    const { data } = await update({
      variables: {
        input: {
          id: connection.id,
          appClientId,
        },
      },
    });
    if (data?.updateSourceConnection.ok) {
      toast.success(`Client ID saved for ${connection.name}`);
      return true;
    }
    toast.error(data?.updateSourceConnection.errors?.[0]?.message ?? "Save failed");
    return false;
  }

  return { saving: loading, save };
}

export type AddClientIdData = ReturnType<typeof useAddClientId>;

interface GenerateResp {
  generateSshDeployKey: MutationResult<AstroliftSshDeployKeyCreated>;
}

/**
 * The GenerateSshDeployKey mutation behind the Generate key sheet. The
 * data half of GenerateSshKeySheet.
 */
export function useGenerateSshKey() {
  const [generate, { loading }] = useMutation<GenerateResp>(GENERATE_SSH_DEPLOY_KEY, {
    refetchQueries: [{ query: LIST_SSH_DEPLOY_KEYS, variables: { appSlug: null } }],
    awaitRefetchQueries: true,
  });

  /** Resolves with the new key on success, null on failure. */
  async function handleGenerate(
    name: string,
    appSlug: string | null
  ): Promise<AstroliftSshDeployKeyCreated["key"] | null> {
    const { data } = await generate({
      variables: {
        input: {
          name,
          appSlug,
        },
      },
    });
    if (data?.generateSshDeployKey.ok && data.generateSshDeployKey.data?.key) {
      toast.success("Key generated — copy the public key into your repo");
      return data.generateSshDeployKey.data.key;
    }
    toast.error(data?.generateSshDeployKey.errors?.[0]?.message ?? "Generation failed");
    return null;
  }

  return { generating: loading, generate: handleGenerate };
}

export type GenerateSshKeyData = ReturnType<typeof useGenerateSshKey>;
