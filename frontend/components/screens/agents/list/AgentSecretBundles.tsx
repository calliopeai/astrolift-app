"use client";

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

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import type {
  AstroliftAgentSecretBundle,
  AstroliftAgentSecretBundleAttachment,
} from "@/graphql/agents/agents.types";

import type { useAgentSecretBundles } from "./use-agent-secret-bundles";

type ConfirmationTarget =
  | { kind: "bundle"; bundle: AstroliftAgentSecretBundle }
  | { kind: "attachment"; attachment: AstroliftAgentSecretBundleAttachment }
  | { kind: "key"; bundle: AstroliftAgentSecretBundle; key: string };

function slugify(value: string): string {
  return value
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
}

export type AgentSecretBundlesViewProps = ReturnType<typeof useAgentSecretBundles>;

/** Reusable secret bundles of an env spec: create, edit, attach, and per-key values. */
export function AgentSecretBundlesView({
  bundles,
  defaultAttachments,
  loading,
  busy,
  reveals,
  onCreate,
  onUpdate,
  onDeleteBundle,
  onAttach,
  onDetach,
  onSetKey,
  onDeleteKey,
  onRevealKey,
}: AgentSecretBundlesViewProps) {
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
  const [confirmationTarget, setConfirmationTarget] = React.useState<ConfirmationTarget | null>(
    null
  );

  async function handleCreate() {
    if (await onCreate(createName, createSlug, createBackendRef)) {
      setCreateName("");
      setCreateSlug("");
      setCreateBackendRef("");
    }
  }

  async function handleUpdate(bundle: AstroliftAgentSecretBundle) {
    if (await onUpdate(bundle, editName, editBackendRef)) setEditingId(null);
  }

  async function handleAttach(
    bundle: AstroliftAgentSecretBundle,
    attachment?: AstroliftAgentSecretBundleAttachment
  ) {
    const prefix = prefixDrafts[bundle.id] ?? attachment?.prefix ?? "";
    const position = positionDrafts[bundle.id] ?? attachment?.position ?? defaultAttachments.length;
    if (await onAttach(bundle, prefix, position, attachment)) {
      setPrefixDrafts((current) => ({ ...current, [bundle.id]: prefix }));
      setPositionDrafts((current) => ({ ...current, [bundle.id]: position }));
    }
  }

  async function handleSetKey(bundle: AstroliftAgentSecretBundle) {
    const draft = keyDrafts[bundle.id] ?? { key: "", value: "" };
    if (await onSetKey(bundle, draft.key, draft.value)) {
      setKeyDrafts((current) => ({ ...current, [bundle.id]: { key: "", value: "" } }));
    }
  }

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
          <Button onClick={handleCreate} disabled={busy === "create"}>
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
                          onClick={() => handleUpdate(bundle)}
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
                            onClick={() => handleAttach(bundle, attachment)}
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
                          <Button size="sm" variant="outline" onClick={() => handleAttach(bundle)}>
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
                        onClick={() => handleSetKey(bundle)}
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
