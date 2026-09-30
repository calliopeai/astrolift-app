"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

import {
  ATTACH_AGENT_SECRET_BUNDLE,
  CREATE_AGENT_SECRET_BUNDLE,
  DELETE_AGENT_BUNDLE_SECRET_VALUE,
  DELETE_AGENT_SECRET_BUNDLE,
  DETACH_AGENT_SECRET_BUNDLE,
  REVEAL_AGENT_BUNDLE_SECRET_VALUE,
  SET_AGENT_BUNDLE_SECRET_VALUE,
  UPDATE_AGENT_SECRET_BUNDLE,
} from "@/graphql/agents/agents.mutations";
import {
  AGENT_SECRET_BUNDLE_ATTACHMENTS,
  AGENT_SECRET_BUNDLES,
} from "@/graphql/agents/agents.queries";
import type {
  AstroliftAgentSecretBundle,
  AstroliftAgentSecretBundleAttachment,
} from "@/graphql/agents/agents.types";

import { usePendingActions } from "@/hooks/use-pending-actions";

type MutationError = { message: string };
type MutationEnvelope<T> = { ok: boolean; errors: MutationError[]; data: T | null };

function errorMessage(payload: MutationEnvelope<unknown> | undefined): string {
  return payload?.errors?.[0]?.message ?? "unknown error";
}

/**
 * The reusable secret bundles of an env spec, their default-environment
 * attachments, and every bundle/key mutation (create, update, delete, attach,
 * detach, set/delete/reveal key). Revealed values auto-hide after 30 seconds.
 * The data half of AgentSecretBundlesView. Handlers the view reacts to
 * resolve true on success.
 */
export function useAgentSecretBundles(envSpecSlug: string, active: boolean) {
  const bundlesQuery = useQuery<{ agentSecretBundles: AstroliftAgentSecretBundle[] }>(
    AGENT_SECRET_BUNDLES,
    {
      variables: { slug: envSpecSlug },
      skip: !active || !envSpecSlug,
      fetchPolicy: "cache-and-network",
    }
  );
  const attachmentsQuery = useQuery<{
    agentEnvironmentSpecSecretBundleAttachments: AstroliftAgentSecretBundleAttachment[];
  }>(AGENT_SECRET_BUNDLE_ATTACHMENTS, {
    variables: { slug: envSpecSlug },
    skip: !active || !envSpecSlug,
    fetchPolicy: "cache-and-network",
  });

  const [createBundle] = useMutation<{
    createAgentSecretBundle: MutationEnvelope<AstroliftAgentSecretBundle>;
  }>(CREATE_AGENT_SECRET_BUNDLE);
  const [updateBundle] = useMutation<{
    updateAgentSecretBundle: MutationEnvelope<AstroliftAgentSecretBundle>;
  }>(UPDATE_AGENT_SECRET_BUNDLE);
  const [deleteBundle] = useMutation<{
    deleteAgentSecretBundle: MutationEnvelope<AstroliftAgentSecretBundle>;
  }>(DELETE_AGENT_SECRET_BUNDLE);
  const [attachBundle] = useMutation<{
    attachAgentSecretBundle: MutationEnvelope<AstroliftAgentSecretBundleAttachment>;
  }>(ATTACH_AGENT_SECRET_BUNDLE);
  const [detachBundle] = useMutation<{
    detachAgentSecretBundle: MutationEnvelope<AstroliftAgentSecretBundleAttachment>;
  }>(DETACH_AGENT_SECRET_BUNDLE);
  const [setBundleKey] = useMutation<{
    setAgentBundleSecretValue: MutationEnvelope<AstroliftAgentSecretBundle>;
  }>(SET_AGENT_BUNDLE_SECRET_VALUE);
  const [deleteBundleKey] = useMutation<{
    deleteAgentBundleSecretValue: MutationEnvelope<AstroliftAgentSecretBundle>;
  }>(DELETE_AGENT_BUNDLE_SECRET_VALUE);
  const [revealBundleKey] = useMutation<{
    revealAgentBundleSecretValue: MutationEnvelope<{ value: string }>;
  }>(REVEAL_AGENT_BUNDLE_SECRET_VALUE);

  const bundles = bundlesQuery.data?.agentSecretBundles ?? [];
  const attachments = attachmentsQuery.data?.agentEnvironmentSpecSecretBundleAttachments ?? [];
  const defaultAttachments = attachments.filter((ref) => ref.environment === "default");

  const [reveals, setReveals] = React.useState<Record<string, string>>({});
  const revealTimers = React.useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  const { pending, begin, finish } = usePendingActions();
  const busy = pending.values().next().value ?? "";

  React.useEffect(
    () => () => Object.values(revealTimers.current).forEach((timer) => clearTimeout(timer)),
    []
  );

  async function refresh() {
    await Promise.all([bundlesQuery.refetch(), attachmentsQuery.refetch()]);
  }

  async function onCreate(name: string, bundleSlug: string, backendRef: string): Promise<boolean> {
    if (!name.trim() || !bundleSlug.trim()) {
      toast.error("Bundle name and slug are required");
      return false;
    }
    const action = "create";
    if (!begin(action)) return false;
    try {
      const response = await createBundle({
        variables: {
          slug: envSpecSlug,
          name: name.trim(),
          bundleSlug: bundleSlug.trim(),
          backendRef: backendRef.trim(),
        },
      });
      const payload = response.data?.createAgentSecretBundle as
        | MutationEnvelope<AstroliftAgentSecretBundle>
        | undefined;
      if (!payload?.ok) throw new Error(errorMessage(payload));
      toast.success("Secret bundle created");
      await refresh();
      return true;
    } catch (error) {
      toast.error(
        `Create bundle failed: ${error instanceof Error ? error.message : String(error)}`
      );
      return false;
    } finally {
      finish(action);
    }
  }

  async function onUpdate(
    bundle: AstroliftAgentSecretBundle,
    name: string,
    backendRef: string
  ): Promise<boolean> {
    const action = `update:${bundle.id}`;
    if (!begin(action)) return false;
    try {
      const response = await updateBundle({
        variables: {
          slug: envSpecSlug,
          bundleId: bundle.id,
          name,
          backendRef,
        },
      });
      const payload = response.data?.updateAgentSecretBundle as
        | MutationEnvelope<AstroliftAgentSecretBundle>
        | undefined;
      if (!payload?.ok) throw new Error(errorMessage(payload));
      toast.success("Bundle updated");
      await refresh();
      return true;
    } catch (error) {
      toast.error(`Update failed: ${error instanceof Error ? error.message : String(error)}`);
      return false;
    } finally {
      finish(action);
    }
  }

  async function onDeleteBundle(bundle: AstroliftAgentSecretBundle) {
    const action = `delete:${bundle.id}`;
    if (!begin(action)) return;
    try {
      const response = await deleteBundle({
        variables: { slug: envSpecSlug, bundleId: bundle.id },
      });
      const payload = response.data?.deleteAgentSecretBundle as
        | MutationEnvelope<AstroliftAgentSecretBundle>
        | undefined;
      if (!payload?.ok) throw new Error(errorMessage(payload));
      toast.success("Bundle deleted");
      await refresh();
    } catch (error) {
      toast.error(
        `Delete bundle failed: ${error instanceof Error ? error.message : String(error)}`
      );
    } finally {
      finish(action);
    }
  }

  async function onAttach(
    bundle: AstroliftAgentSecretBundle,
    prefix: string,
    position: number,
    attachment?: AstroliftAgentSecretBundleAttachment
  ): Promise<boolean> {
    const action = `attach:${bundle.id}`;
    if (!begin(action)) return false;
    try {
      const response = await attachBundle({
        variables: {
          slug: envSpecSlug,
          bundleId: bundle.id,
          prefix,
          position,
        },
      });
      const payload = response.data?.attachAgentSecretBundle as
        | MutationEnvelope<AstroliftAgentSecretBundleAttachment>
        | undefined;
      if (!payload?.ok) throw new Error(errorMessage(payload));
      toast.success(attachment ? "Attachment updated" : "Bundle attached");
      await refresh();
      return true;
    } catch (error) {
      toast.error(`Attach failed: ${error instanceof Error ? error.message : String(error)}`);
      return false;
    } finally {
      finish(action);
    }
  }

  async function onDetach(ref: AstroliftAgentSecretBundleAttachment) {
    const action = `detach:${ref.id}`;
    if (!begin(action)) return;
    try {
      const response = await detachBundle({
        variables: { slug: envSpecSlug, attachmentId: ref.id },
      });
      const payload = response.data?.detachAgentSecretBundle as
        | MutationEnvelope<AstroliftAgentSecretBundleAttachment>
        | undefined;
      if (!payload?.ok) throw new Error(errorMessage(payload));
      toast.success("Bundle detached");
      await refresh();
    } catch (error) {
      toast.error(`Detach failed: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      finish(action);
    }
  }

  async function onSetKey(
    bundle: AstroliftAgentSecretBundle,
    key: string,
    value: string
  ): Promise<boolean> {
    if (!key || !value) {
      toast.error("Key and value are required");
      return false;
    }
    const action = `key:${bundle.id}`;
    if (!begin(action)) return false;
    try {
      const response = await setBundleKey({
        variables: {
          slug: envSpecSlug,
          bundleId: bundle.id,
          key,
          value,
        },
      });
      const payload = response.data?.setAgentBundleSecretValue as
        | MutationEnvelope<AstroliftAgentSecretBundle>
        | undefined;
      if (!payload?.ok) throw new Error(errorMessage(payload));
      toast.success(`Saved ${key}`);
      await refresh();
      return true;
    } catch (error) {
      toast.error(`Save key failed: ${error instanceof Error ? error.message : String(error)}`);
      return false;
    } finally {
      finish(action);
    }
  }

  async function onDeleteKey(bundle: AstroliftAgentSecretBundle, key: string) {
    const action = `key-delete:${bundle.id}:${key}`;
    if (!begin(action)) return;
    try {
      const response = await deleteBundleKey({
        variables: { slug: envSpecSlug, bundleId: bundle.id, key },
      });
      const payload = response.data?.deleteAgentBundleSecretValue as
        | MutationEnvelope<AstroliftAgentSecretBundle>
        | undefined;
      if (!payload?.ok) throw new Error(errorMessage(payload));
      toast.success(`Deleted ${key}`);
      await refresh();
    } catch (error) {
      toast.error(`Delete key failed: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      finish(action);
    }
  }

  async function onRevealKey(bundle: AstroliftAgentSecretBundle, key: string) {
    const revealId = `${bundle.id}:${key}`;
    if (reveals[revealId]) {
      clearTimeout(revealTimers.current[revealId]);
      setReveals((current) => {
        const next = { ...current };
        delete next[revealId];
        return next;
      });
      return;
    }
    const action = `key-reveal:${revealId}`;
    if (!begin(action)) return;
    try {
      const response = await revealBundleKey({
        variables: { slug: envSpecSlug, bundleId: bundle.id, key },
      });
      const payload = response.data?.revealAgentBundleSecretValue as
        | MutationEnvelope<{ value: string }>
        | undefined;
      if (!payload?.ok || !payload.data) throw new Error(errorMessage(payload));
      setReveals((current) => ({ ...current, [revealId]: payload.data!.value }));
      revealTimers.current[revealId] = setTimeout(() => {
        setReveals((current) => {
          const next = { ...current };
          delete next[revealId];
          return next;
        });
      }, 30_000);
    } catch (error) {
      toast.error(`Reveal failed: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      finish(action);
    }
  }

  const loading = bundlesQuery.loading || attachmentsQuery.loading;

  return {
    bundles,
    defaultAttachments,
    loading,
    busy,
    pending,
    reveals,
    onCreate,
    onUpdate,
    onDeleteBundle,
    onAttach,
    onDetach,
    onSetKey,
    onDeleteKey,
    onRevealKey,
  };
}
