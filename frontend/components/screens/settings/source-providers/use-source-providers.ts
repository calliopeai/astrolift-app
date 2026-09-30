"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { toast } from "sonner";

import { useLocalListState } from "@/components/list/use-list-state";
import {
  SECTION_PARAM,
  type SectionSelection,
  sectionHref,
} from "@/components/settings/use-settings-section";
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
  LIST_SSH_DEPLOY_KEYS_PAGE,
} from "@/graphql/scm/scm.queries";
import type {
  AstroliftSourceConnection,
  AstroliftSshDeployKey,
  AstroliftSshDeployKeyCreated,
  AstroliftWebhookSecretReveal,
  MutationResult,
} from "@/graphql/scm/scm.types";

import {
  DEPLOY_KEYS_LIST,
  hostsVariables,
  keysVariables,
  narrowHosts,
  SOURCE_HOSTS_LIST,
} from "./source-providers-list";

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
// search term live in the list state, so no literal variables object names
// the page the operator is actually looking at.
const REFETCH_CONNECTIONS = "ListSourceConnectionsPage";
const REFETCH_KEYS = "ListSshDeployKeysPage";

/**
 * Which Source section is on screen: `?section=hosts|keys`, kept beside the
 * Providers page's `#source` tab hash so a section can be linked and the
 * tab survives the switch.
 */
export function useSourceSection(): SectionSelection {
  const params = useSearchParams();
  const pathname = usePathname() ?? "";
  const router = useRouter();
  const query = params?.toString() ?? "";
  const href = (id: string) => `${sectionHref(pathname, query, id)}#source`;
  return {
    active: params?.get(SECTION_PARAM) ?? null,
    href,
    select: (id) => router.replace(href(id), { scroll: false }),
  };
}

/**
 * The Hosts section: one cursor page of connections (list state in memory,
 * since the Providers page owns its query string) plus the row mutations.
 * The data half of SourceHostsView. The confirm handlers throw on failure:
 * ConfirmDialog keeps itself open and toasts the message.
 */
export function useSourceHosts() {
  // OAuth-callback outcome toasts live in a layout-level component (#759,
  // `components/ScmCallbackToast`) so they surface regardless of where
  // `return_to` lands; the page query cache-and-network-fetches on mount,
  // so the row state is up to date without an explicit refetch here.
  const list = useLocalListState(SOURCE_HOSTS_LIST);
  const page = useQuery<ConnectionsPageResp>(LIST_SOURCE_CONNECTIONS_PAGE, {
    variables: hostsVariables(list.filters, list.state),
    fetchPolicy: "cache-and-network",
  });
  const data = page.data ?? page.previousData;
  const connections = data?.astroliftSourceConnectionsPage;
  const narrowed = narrowHosts(connections?.items ?? [], list.filters);

  // Org-wide connection list, kept for the "needs a Client ID" callout,
  // NOT for rows. The callout enumerates every incomplete GitHub App
  // connection in the org; sourcing it from the page would silently scope
  // the prompt to whichever rows are on screen.
  const conns = useQuery<ConnectionsResp>(LIST_SOURCE_CONNECTIONS);

  const [disconnect, disconnectState] = useMutation<{
    disconnectSource: MutationResult<{ id: string }>;
  }>(DISCONNECT_SOURCE, {
    refetchQueries: [{ query: LIST_SOURCE_CONNECTIONS }, REFETCH_CONNECTIONS],
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

  const refetch = () => {
    void page.refetch();
  };

  return {
    list,
    rows: narrowed.rows,
    totalCount: narrowed.narrowed ? null : (connections?.totalCount ?? null),
    nextCursor: connections?.nextCursor ?? null,
    loading: page.loading && !data,
    error: page.error && !data ? { message: page.error.message } : null,
    onRetry: refetch,
    incompleteClientIdConnections: (conns.data?.astroliftSourceConnections ?? []).filter(
      (c) => c.needsClientId
    ),
    disconnecting: disconnectState.loading,
    rotatingSecret: rotateSecretState.loading,
    disconnect: handleDisconnect,
    rotateSecret: handleRotateSecret,
    // The connect dialogs live in their own files and still refetch the
    // flat list, which does not feed these rows. Refreshing on close is the
    // in-place fix: a dialog that closes after a successful mutation is
    // exactly when the page needs new rows.
    refreshConnections: refetch,
  };
}

export type SourceHostsData = ReturnType<typeof useSourceHosts>;

/**
 * The SSH deploy keys section: one cursor page of keys (list state in
 * memory) and the delete mutation. The data half of DeployKeysView.
 */
export function useDeployKeys() {
  const list = useLocalListState(DEPLOY_KEYS_LIST);
  const page = useQuery<KeysPageResp>(LIST_SSH_DEPLOY_KEYS_PAGE, {
    variables: keysVariables(list.filters, list.state),
    fetchPolicy: "cache-and-network",
  });
  const data = page.data ?? page.previousData;
  const keys = data?.astroliftSshDeployKeysPage;
  // No creator on a key: Mine holds nothing, never everything.
  const mine = Boolean(list.filters.createdBy);

  const [deleteKey, deleteKeyState] = useMutation<{
    deleteSshDeployKey: MutationResult<{ id: string }>;
  }>(DELETE_SSH_DEPLOY_KEY, {
    refetchQueries: [REFETCH_KEYS],
    awaitRefetchQueries: true,
  });

  async function handleDeleteKey(k: AstroliftSshDeployKey) {
    const { data } = await deleteKey({ variables: { input: { id: k.id } } });
    if (data?.deleteSshDeployKey.ok) toast.success(`Deleted ${k.name}`);
    else throw new Error(data?.deleteSshDeployKey.errors?.[0]?.message ?? "Delete failed");
  }

  const refetch = () => {
    void page.refetch();
  };

  return {
    list,
    rows: mine ? [] : (keys?.items ?? []),
    totalCount: mine ? 0 : (keys?.totalCount ?? null),
    nextCursor: mine ? null : (keys?.nextCursor ?? null),
    loading: page.loading && !data,
    error: page.error && !data ? { message: page.error.message } : null,
    onRetry: refetch,
    deletingKey: deleteKeyState.loading,
    deleteKey: handleDeleteKey,
    refreshKeys: refetch,
  };
}

export type DeployKeysData = ReturnType<typeof useDeployKeys>;

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
    refetchQueries: [{ query: LIST_SOURCE_CONNECTIONS }, REFETCH_CONNECTIONS],
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
    refetchQueries: [REFETCH_KEYS],
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
