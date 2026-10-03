"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useRef } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { useLocalListState } from "@/components/list/use-list-state";

import {
  DELETE_PIPELINE_SECRET,
  SET_PIPELINE_SECRET,
} from "@/graphql/pipelines/pipelines.mutations";
import { LIST_PIPELINE_SECRETS } from "@/graphql/pipelines/pipelines.queries";
import type { PipelineSecret } from "@/graphql/pipelines/pipelines.types";

import { PIPELINE_SECRETS_LIST } from "./pipelines-list";

interface SecretsResp {
  astroliftPipelineSecrets: PipelineSecret[];
}

interface MutationResult<T> {
  ok: boolean;
  errors: { code: string; message: string; field?: string | null }[];
  data: T | null;
}

/**
 * The data half of PipelineSecretsView (#100): the secret list (with its
 * list state in memory; the view pages it in the client), set, and delete.
 */
export function usePipelineSecrets(pipelineId: string) {
  const t = useTranslations("PipelineUI");
  const list = useLocalListState(PIPELINE_SECRETS_LIST);
  const {
    data,
    loading,
    error,
    refetch: refetchSecrets,
  } = useQuery<SecretsResp>(LIST_PIPELINE_SECRETS, {
    variables: { pipelineId },
    fetchPolicy: "cache-and-network",
  });

  const [setSecret] = useMutation<{
    setPipelineSecret: MutationResult<{ pipelineId: string; name: string }>;
  }>(SET_PIPELINE_SECRET);

  const [deleteSecretMutation, deleteState] = useMutation<{
    deletePipelineSecret: MutationResult<{ pipelineId: string; name: string }>;
  }>(DELETE_PIPELINE_SECRET);
  const saving = useRef(false);
  const deleting = useRef(false);

  const secrets = data?.astroliftPipelineSecrets ?? [];

  async function refreshAfterCommit() {
    try {
      await refetchSecrets();
    } catch {
      toast.warning(t("secrets.refreshFailed"));
    }
  }

  /** True when the secret was saved, so the add form can close. */
  async function saveSecret(name: string, value: string): Promise<boolean> {
    if (saving.current) return false;
    saving.current = true;
    try {
      const { data: resp } = await setSecret({
        variables: { input: { pipelineId, name, value } },
      });
      if (resp?.setPipelineSecret.ok) {
        toast.success(t("secrets.saved", { name }));
        await refreshAfterCommit();
        return true;
      }
      const err = resp?.setPipelineSecret.errors[0];
      toast.error(err?.message ?? t("secrets.saveFailed"));
      return false;
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("secrets.saveFailed"));
      return false;
    } finally {
      saving.current = false;
    }
  }

  /** Throws on failure so the confirm dialog shows the error inline. */
  async function deleteSecret(secret: PipelineSecret) {
    if (deleting.current) throw new Error(t("secrets.deleteBusy"));
    deleting.current = true;
    try {
      const { data: resp } = await deleteSecretMutation({
        variables: { input: { pipelineId, name: secret.name } },
      });
      if (resp?.deletePipelineSecret.ok) {
        toast.success(t("secrets.deleted", { name: secret.name }));
        await refreshAfterCommit();
      } else {
        throw new Error(resp?.deletePipelineSecret.errors[0]?.message ?? t("secrets.deleteFailed"));
      }
    } finally {
      deleting.current = false;
    }
  }

  return {
    list,
    secrets,
    loading: loading && !data,
    error: data ? null : (error ?? null),
    onRetry: () => {
      void refetchSecrets().catch(() => {});
    },
    deleting: deleteState.loading,
    saveSecret,
    deleteSecret,
  };
}
