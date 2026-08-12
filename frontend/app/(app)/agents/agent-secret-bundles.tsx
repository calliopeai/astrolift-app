"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  EyeIcon,
  EyeOffIcon,
  LinkIcon,
  Loader2Icon,
  PencilIcon,
  PlusIcon,
  SaveIcon,
  Trash2Icon,
  UnlinkIcon,
} from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
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

type MutationError = { message: string };
type MutationEnvelope<T> = { ok: boolean; errors: MutationError[]; data: T | null };
type ConfirmationTarget =
  | { kind: "bundle"; bundle: AstroliftAgentSecretBundle }
  | { kind: "attachment"; attachment: AstroliftAgentSecretBundleAttachment }
  | { kind: "key"; bundle: AstroliftAgentSecretBundle; key: string };

function errorMessage(payload: MutationEnvelope<unknown> | undefined): string {
  return payload?.errors?.[0]?.message ?? "unknown error";
}

function slugify(value: string): string {
  return value
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
}

export function AgentSecretBundles({
  envSpecSlug,
  active,
}: {
  envSpecSlug: string;
  active: boolean;
}) {
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
  const attachedByBundle = new Map(defaultAttachments.map((ref) => [ref.bundleId, ref]));

  const [createName, setCreateName] = React.useState("");
  const [createSlug, setCreateSlug] = React.useState("");
  const [createBackendRef, setCreateBackendRef] = React.useState("");
  const [editingId, setEditingId] = React.useState<string | null>(null);
  const [editName, setEditName] = React.useState("");
  const [editBackendRef, setEditBackendRef] = React.useState("");
  const [keyDrafts, setKeyDrafts] = React.useState<Record<string, { key: string; value: string }>>(
    {}
  );
  const [prefixDrafts, setPrefixDrafts] = React.useState<Record<string, string>>({});
  const [positionDrafts, setPositionDrafts] = React.useState<Record<string, number>>({});
  const [reveals, setReveals] = React.useState<Record<string, string>>({});
  const revealTimers = React.useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  const [busy, setBusy] = React.useState("");
  const [confirmationTarget, setConfirmationTarget] = React.useState<ConfirmationTarget | null>(
    null
  );

  React.useEffect(
    () => () => Object.values(revealTimers.current).forEach((timer) => clearTimeout(timer)),
    []
  );

  async function refresh() {
    await Promise.all([bundlesQuery.refetch(), attachmentsQuery.refetch()]);
  }

  async function onCreate() {
    if (!createName.trim() || !createSlug.trim()) {
      toast.error("Bundle name and slug are required");
      return;
    }
    setBusy("create");
    try {
      const response = await createBundle({
        variables: {
          slug: envSpecSlug,
          name: createName.trim(),
          bundleSlug: createSlug.trim(),
          backendRef: createBackendRef.trim(),
        },
      });
      const payload = response.data?.createAgentSecretBundle as
        | MutationEnvelope<AstroliftAgentSecretBundle>
        | undefined;
      if (!payload?.ok) throw new Error(errorMessage(payload));
      setCreateName("");
      setCreateSlug("");
      setCreateBackendRef("");
      toast.success("Secret bundle created");
      await refresh();
    } catch (error) {
      toast.error(
        `Create bundle failed: ${error instanceof Error ? error.message : String(error)}`
      );
    } finally {
      setBusy("");
    }
  }

  async function onUpdate(bundle: AstroliftAgentSecretBundle) {
    setBusy(`update:${bundle.id}`);
    try {
      const response = await updateBundle({
        variables: {
          slug: envSpecSlug,
          bundleId: bundle.id,
          name: editName,
          backendRef: editBackendRef,
        },
      });
      const payload = response.data?.updateAgentSecretBundle as
        | MutationEnvelope<AstroliftAgentSecretBundle>
        | undefined;
      if (!payload?.ok) throw new Error(errorMessage(payload));
      setEditingId(null);
      toast.success("Bundle updated");
      await refresh();
    } catch (error) {
      toast.error(`Update failed: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setBusy("");
    }
  }

  async function onDeleteBundle(bundle: AstroliftAgentSecretBundle) {
    setBusy(`delete:${bundle.id}`);
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
      setBusy("");
    }
  }

  async function onAttach(
    bundle: AstroliftAgentSecretBundle,
    attachment?: AstroliftAgentSecretBundleAttachment
  ) {
    setBusy(`attach:${bundle.id}`);
    try {
      const prefix = prefixDrafts[bundle.id] ?? attachment?.prefix ?? "";
      const position =
        positionDrafts[bundle.id] ?? attachment?.position ?? defaultAttachments.length;
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
      setPrefixDrafts((current) => ({ ...current, [bundle.id]: prefix }));
      setPositionDrafts((current) => ({ ...current, [bundle.id]: position }));
      toast.success(attachment ? "Attachment updated" : "Bundle attached");
      await refresh();
    } catch (error) {
      toast.error(`Attach failed: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setBusy("");
    }
  }

  async function onDetach(ref: AstroliftAgentSecretBundleAttachment) {
    setBusy(`detach:${ref.id}`);
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
      setBusy("");
    }
  }

  async function onSetKey(bundle: AstroliftAgentSecretBundle) {
    const draft = keyDrafts[bundle.id] ?? { key: "", value: "" };
    if (!draft.key || !draft.value) {
      toast.error("Key and value are required");
      return;
    }
    setBusy(`key:${bundle.id}`);
    try {
      const response = await setBundleKey({
        variables: {
          slug: envSpecSlug,
          bundleId: bundle.id,
          key: draft.key,
          value: draft.value,
        },
      });
      const payload = response.data?.setAgentBundleSecretValue as
        | MutationEnvelope<AstroliftAgentSecretBundle>
        | undefined;
      if (!payload?.ok) throw new Error(errorMessage(payload));
      setKeyDrafts((current) => ({ ...current, [bundle.id]: { key: "", value: "" } }));
      toast.success(`Saved ${draft.key}`);
      await refresh();
    } catch (error) {
      toast.error(`Save key failed: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setBusy("");
    }
  }

  async function onDeleteKey(bundle: AstroliftAgentSecretBundle, key: string) {
    setBusy(`key-delete:${bundle.id}:${key}`);
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
      setBusy("");
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
    setBusy(`key-reveal:${revealId}`);
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
      setBusy("");
    }
  }

  const loading = bundlesQuery.loading || attachmentsQuery.loading;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Reusable secret bundles</CardTitle>
        <CardDescription>
          Share a provider-backed key set across agents. Bundles merge in attachment order; direct
          refs above override a bundle key with the same environment variable.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="grid gap-2 rounded-md border p-3 md:grid-cols-[1fr_1fr_1.4fr_auto]">
          <Input
            placeholder="Bundle name"
            value={createName}
            onChange={(event) => {
              setCreateName(event.target.value);
              if (!createSlug) setCreateSlug(slugify(event.target.value));
            }}
          />
          <Input
            placeholder="bundle-slug"
            value={createSlug}
            onChange={(e) => setCreateSlug(e.target.value)}
          />
          <Input
            placeholder="Provider ref (optional)"
            value={createBackendRef}
            onChange={(e) => setCreateBackendRef(e.target.value)}
          />
          <Button onClick={onCreate} disabled={busy === "create"}>
            {busy === "create" ? (
              <Loader2Icon className="size-4 animate-spin" />
            ) : (
              <PlusIcon className="size-4" />
            )}
            Create
          </Button>
        </div>

        {loading && bundles.length === 0 ? (
          <div className="text-muted-foreground flex items-center gap-2 text-sm">
            <Loader2Icon className="size-4 animate-spin" /> Loading bundles…
          </div>
        ) : bundles.length === 0 ? (
          <p className="text-muted-foreground text-sm">No reusable bundles yet.</p>
        ) : (
          <div className="space-y-3">
            {bundles.map((bundle) => {
              const attachment = attachedByBundle.get(bundle.id);
              const draft = keyDrafts[bundle.id] ?? { key: "", value: "" };
              const editing = editingId === bundle.id;
              return (
                <div key={bundle.id} className="space-y-3 rounded-md border p-3">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      {editing ? (
                        <div className="grid gap-2 md:grid-cols-2">
                          <Input value={editName} onChange={(e) => setEditName(e.target.value)} />
                          <Input
                            value={editBackendRef}
                            onChange={(e) => setEditBackendRef(e.target.value)}
                          />
                        </div>
                      ) : (
                        <>
                          <div className="flex flex-wrap items-center gap-2">
                            <p className="font-medium">{bundle.name}</p>
                            <Badge variant="outline">{bundle.provider}</Badge>
                            {attachment && <Badge>Attached #{attachment.position + 1}</Badge>}
                          </div>
                          <p className="text-muted-foreground truncate font-mono text-xs">
                            {bundle.backendRef}
                          </p>
                          {!bundle.canReveal && bundle.readLimitation && (
                            <p className="text-muted-foreground mt-1 text-xs">
                              {bundle.readLimitation}
                            </p>
                          )}
                        </>
                      )}
                    </div>
                    <div className="flex flex-wrap items-center gap-1">
                      {editing ? (
                        <Button
                          size="sm"
                          onClick={() => onUpdate(bundle)}
                          disabled={busy === `update:${bundle.id}`}
                        >
                          <SaveIcon className="size-4" /> Save
                        </Button>
                      ) : (
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => {
                            setEditingId(bundle.id);
                            setEditName(bundle.name);
                            setEditBackendRef(bundle.backendRef);
                          }}
                        >
                          <PencilIcon className="size-4" /> Edit
                        </Button>
                      )}
                      {attachment ? (
                        <>
                          <Input
                            className="h-8 w-28"
                            aria-label={`Prefix for ${bundle.name}`}
                            placeholder="ENV_ prefix"
                            value={prefixDrafts[bundle.id] ?? attachment.prefix}
                            onChange={(event) =>
                              setPrefixDrafts((current) => ({
                                ...current,
                                [bundle.id]: event.target.value,
                              }))
                            }
                          />
                          <Input
                            className="h-8 w-20"
                            type="number"
                            min={0}
                            aria-label={`Merge position for ${bundle.name}`}
                            value={positionDrafts[bundle.id] ?? attachment.position}
                            onChange={(event) =>
                              setPositionDrafts((current) => ({
                                ...current,
                                [bundle.id]: Math.max(0, Number(event.target.value) || 0),
                              }))
                            }
                          />
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => onAttach(bundle, attachment)}
                            disabled={busy === `attach:${bundle.id}`}
                          >
                            <SaveIcon className="size-4" /> Save attachment
                          </Button>
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() =>
                              setConfirmationTarget({ kind: "attachment", attachment })
                            }
                          >
                            <UnlinkIcon className="size-4" /> Detach
                          </Button>
                        </>
                      ) : (
                        <>
                          <Input
                            className="h-8 w-28"
                            placeholder="ENV_ prefix"
                            value={prefixDrafts[bundle.id] ?? ""}
                            onChange={(e) =>
                              setPrefixDrafts((current) => ({
                                ...current,
                                [bundle.id]: e.target.value,
                              }))
                            }
                          />
                          <Button size="sm" variant="outline" onClick={() => onAttach(bundle)}>
                            <LinkIcon className="size-4" /> Attach
                          </Button>
                        </>
                      )}
                      <Button
                        size="icon-sm"
                        variant="ghost"
                        aria-label={`Delete ${bundle.name}`}
                        onClick={() => setConfirmationTarget({ kind: "bundle", bundle })}
                      >
                        <Trash2Icon className="size-4" />
                      </Button>
                    </div>
                  </div>

                  <div className="space-y-2">
                    {bundle.keyNames.map((key) => {
                      const revealId = `${bundle.id}:${key}`;
                      return (
                        <div
                          key={key}
                          className="bg-muted/40 flex items-center gap-2 rounded px-2 py-1.5"
                        >
                          <span className="min-w-0 flex-1 truncate font-mono text-xs">{key}</span>
                          {reveals[revealId] && (
                            <code className="bg-background max-w-[45%] truncate rounded px-2 py-1 text-xs">
                              {reveals[revealId]}
                            </code>
                          )}
                          <Button
                            size="icon-sm"
                            variant="ghost"
                            disabled={!bundle.canReveal || busy === `key-reveal:${revealId}`}
                            onClick={() => onRevealKey(bundle, key)}
                            aria-label={`${reveals[revealId] ? "Hide" : "Reveal"} ${key}`}
                          >
                            {reveals[revealId] ? (
                              <EyeOffIcon className="size-4" />
                            ) : (
                              <EyeIcon className="size-4" />
                            )}
                          </Button>
                          <Button
                            size="icon-sm"
                            variant="ghost"
                            onClick={() => setConfirmationTarget({ kind: "key", bundle, key })}
                            aria-label={`Delete ${key}`}
                          >
                            <Trash2Icon className="size-4" />
                          </Button>
                        </div>
                      );
                    })}
                    <div className="grid gap-2 md:grid-cols-[1fr_1.6fr_auto]">
                      <Input
                        placeholder="ENV_KEY"
                        value={draft.key}
                        onChange={(e) =>
                          setKeyDrafts((current) => ({
                            ...current,
                            [bundle.id]: { ...draft, key: e.target.value },
                          }))
                        }
                      />
                      <Input
                        type="password"
                        autoComplete="new-password"
                        placeholder="Value"
                        value={draft.value}
                        onChange={(e) =>
                          setKeyDrafts((current) => ({
                            ...current,
                            [bundle.id]: { ...draft, value: e.target.value },
                          }))
                        }
                      />
                      <Button
                        size="sm"
                        onClick={() => onSetKey(bundle)}
                        disabled={!draft.key || !draft.value}
                      >
                        <PlusIcon className="size-4" /> Set key
                      </Button>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </CardContent>
      <ConfirmDialog
        open={confirmationTarget !== null}
        onOpenChange={(next) => {
          if (!next) setConfirmationTarget(null);
        }}
        title={
          confirmationTarget?.kind === "bundle"
            ? `Delete “${confirmationTarget.bundle.name}”?`
            : confirmationTarget?.kind === "attachment"
              ? `Detach “${confirmationTarget.attachment.bundleName}”?`
              : confirmationTarget?.kind === "key"
                ? `Delete ${confirmationTarget.key}?`
                : "Confirm secret change"
        }
        description={
          confirmationTarget?.kind === "bundle"
            ? "This deletes both the bundle definition and its provider-side secret. The provider's recovery policy may allow restoration for a limited time."
            : confirmationTarget?.kind === "attachment"
              ? "This agent will no longer receive values from the bundle. The bundle and its provider-side values are retained."
              : confirmationTarget?.kind === "key"
                ? `This permanently deletes ${confirmationTarget.key} from “${confirmationTarget.bundle.name}” in the configured secret provider.`
                : undefined
        }
        confirmLabel={confirmationTarget?.kind === "attachment" ? "Detach bundle" : "Delete"}
        destructive
        onConfirm={async () => {
          if (!confirmationTarget) return;
          if (confirmationTarget.kind === "bundle") {
            await onDeleteBundle(confirmationTarget.bundle);
          } else if (confirmationTarget.kind === "attachment") {
            await onDetach(confirmationTarget.attachment);
          } else {
            await onDeleteKey(confirmationTarget.bundle, confirmationTarget.key);
          }
        }}
      />
    </Card>
  );
}
