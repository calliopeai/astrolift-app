"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { hasErrors, splitErrors } from "@/components/screens/agents/skills/catalog";
import {
  TOOL_FIELDS,
  type ToolDefFields,
  type ToolErrors,
  validateTool,
} from "@/components/screens/agents/skills/use-add-tool";
import { DELETE_TOOL_DEF, UPDATE_TOOL_DEF } from "@/graphql/agents/agents.mutations";
import { GET_TOOL_DEF } from "@/graphql/agents/agents.queries";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";

// ─── Types ────────────────────────────────────────────────────────────────────

export type { Adapter as ToolAdapter } from "@/components/screens/agents/skills/tool-adapters";
export type { ToolErrors } from "@/components/screens/agents/skills/use-add-tool";

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
export type ToolDetailFormValues = ToolDefFields;

type ToolDefData = { toolDef: ToolDetailTool | null };

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

  // `toolDef(id)` is scoped to the org's own and global skills (#2155); the
  // org id only gates the read until the active org is known.
  const { data, loading, error, refetch } = useQuery<ToolDefData>(GET_TOOL_DEF, {
    variables: { id },
    fetchPolicy: "cache-and-network",
    skip: !orgId,
  });

  const tool = data?.toolDef ?? null;

  const [updateToolDef, { loading: saving }] = useMutation<UpdateToolDefData>(UPDATE_TOOL_DEF, {
    refetchQueries: ["ListOrgToolDefs", "ListToolDefs", "ToolDefsListPage", "GetToolDef"],
  });

  const [deleteToolDef, { loading: deleting }] = useMutation<DeleteToolDefData>(DELETE_TOOL_DEF, {
    refetchQueries: ["ListOrgToolDefs", "ListToolDefs", "ToolDefsListPage"],
  });

  /** Resolves the errors to show beside their fields; empty when it saved. */
  async function save(values: ToolDetailFormValues): Promise<ToolErrors> {
    const invalid = validateTool(values);
    if (hasErrors(invalid)) return invalid;
    const { name, slug, description, adapter, handlerRef } = values;
    try {
      const { data: mutData } = await updateToolDef({
        variables: {
          id,
          input: {
            name: name.trim(),
            slug: slug.trim(),
            description: description.trim(),
            adapter,
            handlerRef: handlerRef.trim(),
            inputSchema: JSON.parse(values.inputSchemaText.trim() || "{}"),
            outputSchema: JSON.parse(values.outputSchemaText.trim() || "{}"),
            implementationConfig: null,
          },
        },
      });
      if (mutData?.updateToolDef?.ok) {
        toast.success("Tool saved");
        return {};
      }
      const errors = splitErrors(mutData?.updateToolDef?.errors ?? [], TOOL_FIELDS);
      return hasErrors(errors) ? errors : { form: "The tool was not saved." };
    } catch (err) {
      return { form: err instanceof Error ? err.message : String(err) };
    }
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
    loading: loading && !data,
    error: error ? { message: error.message } : null,
    onRetry: () => {
      void refetch();
    },
    saving,
    deleting,
    save,
    remove,
  };
}
