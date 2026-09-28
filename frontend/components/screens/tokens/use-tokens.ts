"use client";

import { useMutation } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

import { useCursorTable } from "@/components/data-table";
import { CREATE_API_TOKEN, REVOKE_API_TOKEN } from "@/graphql/identity/identity.mutations";
import { LIST_API_TOKENS_PAGE } from "@/graphql/identity/identity.queries";
import type {
  AstroliftApiToken,
  AstroliftApiTokenPlaintext,
  MutationResult,
} from "@/graphql/identity/identity.types";

interface Resp {
  astroliftApiTokensPage: {
    items: AstroliftApiToken[];
    nextCursor?: string | null;
    totalCount?: number | null;
  };
}

export interface CreateApiTokenInput {
  name: string;
  /** Raw field value; 0 or empty means no expiry. */
  expiresInDays: string;
  scopes: string[];
}

/**
 * The org API keys page: the paged token table, create / revoke, the
 * one-time plaintext reveal, and the MCP endpoint. The data half of
 * TokensScreen.
 */
export function useTokens() {
  const [createdToken, setCreatedToken] = React.useState<AstroliftApiTokenPlaintext | null>(null);
  const [mcpEndpoint, setMcpEndpoint] = React.useState("/api/mcp/v1/");

  React.useEffect(() => {
    setMcpEndpoint(`${window.location.origin}/api/mcp/v1/`);
  }, []);

  const table = useCursorTable<AstroliftApiToken>({
    query: LIST_API_TOKENS_PAGE,
    extract: (d) => (d as Resp | undefined)?.astroliftApiTokensPage,
    searchVariable: "search",
    urlKey: "tok",
  });

  // Refetched by operation name, not by document: the walk's cursor, page
  // size and search term live in the controller, so only the active query
  // knows the variables of the page the operator is looking at.
  const refetchPage = ["ListApiTokensPage"];

  const [createToken, { loading: creating }] = useMutation<{
    createApiToken: MutationResult<AstroliftApiTokenPlaintext>;
  }>(CREATE_API_TOKEN, {
    refetchQueries: refetchPage,
    awaitRefetchQueries: true,
  });

  const [revokeToken, { loading: revoking }] = useMutation<{
    revokeApiToken: MutationResult<{ id: string; deleted: boolean }>;
  }>(REVOKE_API_TOKEN, {
    refetchQueries: refetchPage,
    awaitRefetchQueries: true,
  });

  /** Resolves true once the token is minted, so the sheet resets and closes. */
  async function onCreate(input: CreateApiTokenInput): Promise<boolean> {
    if (input.scopes.length === 0) {
      toast.error("Pick at least one scope");
      return false;
    }
    const { data } = await createToken({
      variables: {
        input: {
          name: input.name.trim(),
          expiresInDays: Number(input.expiresInDays) || null,
          scopes: input.scopes,
        },
      },
    });
    if (data?.createApiToken.ok && data.createApiToken.data) {
      setCreatedToken(data.createApiToken.data);
      return true;
    }
    toast.error(data?.createApiToken.errors?.[0]?.message ?? "Create failed");
    return false;
  }

  /** Throws on failure so the confirm dialog stays open and shows the error. */
  async function onRevoke(t: AstroliftApiToken) {
    const { data } = await revokeToken({ variables: { input: { id: t.id } } });
    if (data?.revokeApiToken.ok) {
      toast.success("Revoked");
    } else {
      throw new Error(data?.revokeApiToken.errors?.[0]?.message ?? "Revoke failed");
    }
  }

  function onCopyPlaintext() {
    if (!createdToken) return;
    navigator.clipboard.writeText(createdToken.plaintext);
    toast.success("Copied to clipboard");
  }

  function onCopyMcpEndpoint() {
    navigator.clipboard.writeText(mcpEndpoint);
    toast.success("MCP endpoint copied");
  }

  return {
    table,
    creating,
    revoking,
    createdToken,
    onDismissCreated: () => setCreatedToken(null),
    mcpEndpoint,
    onCreate,
    onRevoke,
    onCopyPlaintext,
    onCopyMcpEndpoint,
  };
}
