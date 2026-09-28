"use client";

import { gql } from "@apollo/client";
import { useMutation, useQuery } from "@apollo/client/react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { DELETE_TOOL_DEF, UPDATE_TOOL_DEF } from "@/graphql/agents/agents.mutations";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";

// ─── GraphQL ─────────────────────────────────────────────────────────────────

// The global toolDefs resolver is skill-scoped, so we fetch the single
// tool def via the list query on its parent skill — but we don't have
// the parent skill from the URL here. Use a local inline query on the
// orgToolDefs resolver instead, filtering client-side by ID.
// Alternatively, use a dedicated GET_TOOL_DEF query once it exists.
// For now we re-use LIST_ORG_TOOL_DEFS and find by id param.

const GET_TOOL_DEF = gql`
  query GetToolDef($orgId: ID!, $id: ID!) {
    orgToolDefs(orgId: $orgId) {
      id
      name
      slug
      description
      adapter
      inputSchema
      outputSchema
      handlerRef
      createdAt
    }
  }
`;

// ─── Types ────────────────────────────────────────────────────────────────────

export type ToolAdapter = "python_fn" | "http_endpoint" | "mcp_server";

export type ToolDetailTool = {
  id: string;
  name: string;
  slug: string;
  description: string;
  adapter: string;
  handlerRef: string;
  inputSchema: unknown;
  outputSchema: unknown;
  createdAt: string;
};

/** What the form hands the hook on submit, schemas still as raw text. */
export type ToolDetailFormValues = {
  name: string;
  slug: string;
  description: string;
  adapter: ToolAdapter;
  handlerRef: string;
  inputSchemaText: string;
  outputSchemaText: string;
};

/**
 * ok: the save went through. schemaError: set when a schema failed to parse
 * (a string) or parsed cleanly (null); absent when the save stopped before
 * the schemas were checked.
 */
export type ToolDetailSaveResult = { ok: boolean; schemaError?: string | null };

type OrgToolDefsData = { orgToolDefs: ToolDetailTool[] };

type MutError = { field: string; message: string; code: string };

type UpdateToolDefData = {
  updateToolDef: {
    ok: boolean;
    errors: MutError[];
    data: { id: string; name: string; slug: string; adapter: string } | null;
  };
};

type DeleteToolDefData = {
  deleteToolDef: { ok: boolean; errors: MutError[] };
};

/**
 * One tool definition plus its update and delete mutations. The data half
 * of ToolDetailScreen.
 */
export function useToolDetail(id: string) {
  const router = useRouter();
  // Reactive org id (#1022): the synchronous cookie read races the
  // post-render effect that sets it, leaving orgId "" on cold load.
  const { org } = useActiveOrg();
  const orgId = org?.id ?? "";

  const { data, loading, error } = useQuery<OrgToolDefsData>(GET_TOOL_DEF, {
    variables: { orgId, id },
    fetchPolicy: "cache-and-network",
    skip: !orgId,
  });

  const tool = data?.orgToolDefs?.find((t) => t.id === id) ?? null;

  const [updateToolDef, { loading: saving }] = useMutation<UpdateToolDefData>(UPDATE_TOOL_DEF, {
    refetchQueries: ["ListOrgToolDefs", "ListToolDefs"],
  });

  const [deleteToolDef, { loading: deleting }] = useMutation<DeleteToolDefData>(DELETE_TOOL_DEF, {
    refetchQueries: ["ListOrgToolDefs", "ListToolDefs"],
  });

  async function save(values: ToolDetailFormValues): Promise<ToolDetailSaveResult> {
    const { name, slug, description, adapter, handlerRef } = values;
    if (!name.trim() || !slug.trim()) {
      toast.error("Name and slug are required");
      return { ok: false };
    }
    let parsedInput: unknown = {};
    let parsedOutput: unknown = {};
    try {
      parsedInput = JSON.parse(values.inputSchemaText);
    } catch {
      return { ok: false, schemaError: "Input schema is not valid JSON" };
    }
    try {
      parsedOutput = JSON.parse(values.outputSchemaText);
    } catch {
      return { ok: false, schemaError: "Output schema is not valid JSON" };
    }
    const { data: mutData } = await updateToolDef({
      variables: {
        id,
        input: {
          name: name.trim(),
          slug: slug.trim(),
          description: description.trim(),
          adapter,
          handlerRef: handlerRef.trim(),
          inputSchema: parsedInput,
          outputSchema: parsedOutput,
          implementationConfig: null,
        },
      },
    });
    if (mutData?.updateToolDef?.ok) {
      toast.success("Tool saved");
      return { ok: true, schemaError: null };
    }
    for (const err of mutData?.updateToolDef?.errors ?? []) {
      toast.error(`${err.field}: ${err.message}`);
    }
    return { ok: false, schemaError: null };
  }

  // Throws on failure so ConfirmDialog holds the dialog open and toasts
  // the message, instead of closing on a delete that never happened.
  async function remove() {
    const { data: mutData } = await deleteToolDef({ variables: { id } });
    if (mutData?.deleteToolDef?.ok) {
      toast.success("Tool deleted");
      router.push("/agents/tools");
    } else {
      throw new Error("Failed to delete tool");
    }
  }

  return {
    tool,
    loading,
    error: error ? { message: error.message } : null,
    saving,
    deleting,
    save,
    remove,
  };
}
