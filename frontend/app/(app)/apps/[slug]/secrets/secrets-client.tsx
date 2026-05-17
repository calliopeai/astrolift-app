"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  CheckIcon,
  EyeIcon,
  EyeOffIcon,
  KeyIcon,
  LayersIcon,
  Loader2Icon,
  PencilIcon,
  PlusIcon,
  Trash2Icon,
  UploadIcon,
  XIcon,
} from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import type { MutationResult } from "@/graphql/identity/identity.types";
import { LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftAppEnvironment } from "@/graphql/lifecycle/lifecycle.types";
import {
  BULK_IMPORT_APP_SECRETS,
  DELETE_APP_SECRET,
  DETACH_SECRET_BUNDLE,
  REVEAL_APP_SECRET,
  SET_APP_SECRET,
} from "@/graphql/services/services.mutations";
import {
  GET_APP_VERSION,
  LIST_APP_SECRETS,
  LIST_APP_SECRET_BUNDLE_ATTACHMENTS,
  LIST_SECRET_CHANGE_PROPOSALS,
} from "@/graphql/services/services.queries";
import type { AstroliftSecretChangeProposal } from "@/graphql/services/services.types";
import { handleVersionMismatch } from "@/lib/apollo/version-mismatch";

import { AppTabs } from "../components/app-tabs";

const ALL_ENVS = "__all__";

interface SecretEditor {
  id: string;
  username: string;
  displayName: string;
}

interface AppSecret {
  id: string;
  key: string;
  environmentName: string;
  source: string;
  bundleSlug: string;
  managedServiceKind: string;
  isMasked: boolean;
  lastEditedAt?: string | null;
  lastEditedBy?: SecretEditor | null;
}

interface AppSecretBundleAttachment {
  id: string;
  registeredAppSlug: string;
  environmentName: string;
  bundleSlug: string;
  bundleName: string;
  prefix: string;
  teamSlug?: string | null;
  keyCount: number;
  mergeOrder: number;
  attachedAt?: string | null;
}

interface SecretsResp {
  astroliftAppSecrets: AppSecret[];
}

interface EnvsResp {
  astroliftEnvironments: AstroliftAppEnvironment[];
}

interface AttachmentsResp {
  astroliftAppSecretBundleAttachments: AppSecretBundleAttachment[];
}

interface RevealedSecretData {
  secretId: string;
  key: string;
  environmentName: string;
  value: string;
  revealedAt: string;
}

const SOURCE_LABEL: Record<string, string> = {
  literal: "literal",
  bundle: "bundle",
  managed_service: "managed",
};

const SOURCE_TONE: Record<string, "secondary" | "outline" | "default"> = {
  literal: "secondary",
  bundle: "outline",
  managed_service: "default",
};

export function SecretsClient({ slug }: { slug: string }) {
  const t = useTranslations("apps.secrets");
  const [envName, setEnvName] = React.useState<string>(ALL_ENVS);
  const [setOpen, setSetOpen] = React.useState(false);
  const [bulkOpen, setBulkOpen] = React.useState(false);
  const [deleteTarget, setDeleteTarget] = React.useState<AppSecret | null>(null);
  const [detachTarget, setDetachTarget] = React.useState<AppSecretBundleAttachment | null>(null);
  // revealedValues maps secret.id -> plaintext while revealed.
  const [revealedValues, setRevealedValues] = React.useState<Record<string, string>>({});
  // editingId: which row is in inline-edit mode (must be revealed first).
  const [editingId, setEditingId] = React.useState<string | null>(null);
  // revealingId: which row's reveal mutation is currently in flight.
  // Tracked locally because Apollo's useMutation result doesn't expose
  // the in-flight input variables in a typed way across SDK versions.
  const [revealingId, setRevealingId] = React.useState<string | null>(null);

  const variables = {
    appSlug: slug,
    environmentName: envName === ALL_ENVS ? null : envName,
  };

  const envs = useQuery<EnvsResp>(LIST_ENVIRONMENTS, {
    variables: { appSlug: slug },
    fetchPolicy: "cache-and-network",
  });
  // #497 — fetch the app's current ``version`` so every
  // ``setAppSecret`` mutation can carry ``ifMatchVersion``. Cheap query
  // (id + version only); Apollo will merge into any other cache entry
  // for this app slug.
  const appVersion = useQuery<{ astroliftApp: { id: string; version: number } | null }>(
    GET_APP_VERSION,
    { variables: { slug }, fetchPolicy: "cache-and-network" },
  );
  const secrets = useQuery<SecretsResp>(LIST_APP_SECRETS, {
    variables,
    fetchPolicy: "cache-and-network",
  });
  const attachments = useQuery<AttachmentsResp>(LIST_APP_SECRET_BUNDLE_ATTACHMENTS, {
    variables,
    fetchPolicy: "cache-and-network",
  });
  // #488 — show inline "N pending proposal" banner when the app has
  // pending secret-change proposals. Poll lazily; the banner is
  // ambient context, not a primary action surface.
  const pendingProposals = useQuery<{
    astroliftSecretChangeProposals: AstroliftSecretChangeProposal[];
  }>(LIST_SECRET_CHANGE_PROPOSALS, {
    variables: { appSlug: slug, status: "pending" },
    fetchPolicy: "cache-and-network",
    pollInterval: 60_000,
  });

  const refetch = [
    { query: LIST_APP_SECRETS, variables },
    {
      query: LIST_APP_SECRET_BUNDLE_ATTACHMENTS,
      variables,
    },
  ];

  const [setSecret, setState] = useMutation<{
    setAppSecret: MutationResult<{
      appSlug: string;
      key: string;
      rawManifestStaged: string;
    }>;
  }>(SET_APP_SECRET, { refetchQueries: refetch, awaitRefetchQueries: true });
  const [deleteSecret, deleteState] = useMutation<{
    deleteAppSecret: MutationResult<{
      appSlug: string;
      key: string;
      rawManifestStaged: string;
    }>;
  }>(DELETE_APP_SECRET, {
    refetchQueries: refetch,
    awaitRefetchQueries: true,
  });
  const [bulkImport, bulkState] = useMutation<{
    bulkImportAppSecrets: MutationResult<{
      appSlug: string;
      keysSet: string[];
      rawManifestStaged: string;
    }>;
  }>(BULK_IMPORT_APP_SECRETS, {
    refetchQueries: refetch,
    awaitRefetchQueries: true,
  });
  const [detachBundle, detachState] = useMutation<{
    detachSecretBundle: MutationResult<{ attachmentId: string }>;
  }>(DETACH_SECRET_BUNDLE, {
    refetchQueries: refetch,
    awaitRefetchQueries: true,
  });
  const [revealSecret] = useMutation<{
    revealAppSecret: MutationResult<RevealedSecretData>;
  }>(REVEAL_APP_SECRET);

  const busy = setState.loading || deleteState.loading || bulkState.loading || detachState.loading;
  const list = secrets.data?.astroliftAppSecrets ?? [];
  const envList = envs.data?.astroliftEnvironments ?? [];
  const attachmentList = attachments.data?.astroliftAppSecretBundleAttachments ?? [];

  function requestDelete(s: AppSecret) {
    if (s.source !== "literal") {
      toast.error(
        s.source === "bundle"
          ? "Detach the bundle to remove this key."
          : "Managed-service envelope keys aren't editable directly."
      );
      return;
    }
    setDeleteTarget(s);
  }

  async function handleDelete(s: AppSecret) {
    const { data } = await deleteSecret({
      variables: { input: { appSlug: slug, key: s.key } },
    });
    if (data?.deleteAppSecret.ok) {
      toast.success(`Deleted ${s.key}`);
      setRevealedValues((prev) => {
        if (!(s.id in prev)) return prev;
        const next = { ...prev };
        delete next[s.id];
        return next;
      });
    } else {
      throw new Error(data?.deleteAppSecret.errors?.[0]?.message ?? "Delete failed");
    }
  }

  async function handleReveal(s: AppSecret) {
    if (s.source !== "literal") {
      toast.error(t("reveal.literalOnly"));
      return;
    }
    if (s.id in revealedValues) {
      setRevealedValues((prev) => {
        const next = { ...prev };
        delete next[s.id];
        return next;
      });
      if (editingId === s.id) setEditingId(null);
      return;
    }
    setRevealingId(s.id);
    try {
      const { data } = await revealSecret({
        variables: { input: { appSlug: slug, secretId: s.id } },
      });
      if (data?.revealAppSecret.ok && data.revealAppSecret.data) {
        const value = data.revealAppSecret.data.value;
        setRevealedValues((prev) => ({ ...prev, [s.id]: value }));
        toast.success(t("reveal.toastRevealed"));
      } else {
        const err = data?.revealAppSecret.errors?.[0];
        if (err?.code === "PERMISSION_DENIED") {
          toast.error(t("reveal.permissionDenied"));
        } else {
          toast.error(err?.message ?? t("reveal.failed"));
        }
      }
    } catch (err) {
      toast.error((err as Error).message ?? t("reveal.failed"));
    } finally {
      setRevealingId(null);
    }
  }

  async function handleInlineSave(s: AppSecret, nextValue: string) {
    const { data } = await setSecret({
      variables: {
        input: {
          appSlug: slug,
          key: s.key,
          value: nextValue,
          ifMatchVersion: appVersion.data?.astroliftApp?.version ?? null,
        },
      },
    });
    if (data?.setAppSecret.ok) {
      toast.success(t("edit.toastSaved", { key: s.key }));
      setRevealedValues((prev) => ({ ...prev, [s.id]: nextValue }));
      setEditingId(null);
      return true;
    }
    if (handleVersionMismatch(data?.setAppSecret, { label: "app", onRefresh: () => appVersion.refetch() })) {
      return false;
    }
    toast.error(data?.setAppSecret.errors?.[0]?.message ?? "Save failed");
    return false;
  }

  async function handleDetach(a: AppSecretBundleAttachment) {
    const { data } = await detachBundle({
      variables: { input: { attachmentId: a.id } },
    });
    if (data?.detachSecretBundle.ok) {
      toast.success(t("attached.toastDetached", { name: a.bundleName }));
    } else {
      throw new Error(data?.detachSecretBundle.errors?.[0]?.message ?? "Detach failed");
    }
  }

  return (
    <PageShell
      title={t("title")}
      description={
        <span className="text-muted-foreground font-mono text-xs">
          {t("description", { slug })}
        </span>
      }
      actions={
        <>
          <Can permission="app.deploy">
            <Button variant="outline" onClick={() => setBulkOpen(true)}>
              <UploadIcon className="size-4" />
              {t("bulkImport")}
            </Button>
          </Can>
          <Can permission="app.deploy">
            <Button onClick={() => setSetOpen(true)}>
              <PlusIcon className="size-4" />
              {t("newSecret")}
            </Button>
          </Can>
        </>
      }
    >
      <AppTabs slug={slug} active="secrets" />

      <PendingProposalsBanner
        proposals={pendingProposals.data?.astroliftSecretChangeProposals ?? []}
      />

      <div className="flex flex-wrap items-center gap-2">
        <Label className="text-muted-foreground text-xs tracking-wide uppercase">
          {t("environment")}
        </Label>
        <Select value={envName} onValueChange={setEnvName}>
          <SelectTrigger className="w-56">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL_ENVS}>{t("allEnvironments")}</SelectItem>
            {envList.map((e) => (
              <SelectItem key={e.id} value={e.name}>
                {e.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <span className="text-muted-foreground text-xs">
          {t("keysCount", { count: list.length })}
        </span>
      </div>

      <AttachedBundlesSection
        loading={attachments.loading && attachmentList.length === 0}
        attachments={attachmentList}
        onDetach={(a) => setDetachTarget(a)}
        busy={detachState.loading}
      />

      <Card>
        <CardContent className="p-0">
          {secrets.loading && list.length === 0 ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : list.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<KeyIcon className="size-5" />}
                title={t("emptyTitle")}
                description={t("emptyDescription")}
              />
            </div>
          ) : (
            <TooltipProvider delayDuration={200}>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t("columns.key")}</TableHead>
                    <TableHead>{t("columns.value")}</TableHead>
                    <TableHead>{t("columns.source")}</TableHead>
                    <TableHead>{t("columns.env")}</TableHead>
                    <TableHead>{t("columns.lastEdited")}</TableHead>
                    <TableHead className="w-12 text-right"></TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {list.map((s) => (
                    <SecretRow
                      key={s.id}
                      secret={s}
                      revealed={s.id in revealedValues ? revealedValues[s.id] : null}
                      editing={editingId === s.id}
                      onToggleReveal={() => handleReveal(s)}
                      onStartEdit={() => setEditingId(s.id)}
                      onCancelEdit={() => setEditingId(null)}
                      onSave={(value) => handleInlineSave(s, value)}
                      onRequestDelete={() => requestDelete(s)}
                      busy={busy}
                      revealing={revealingId === s.id}
                    />
                  ))}
                </TableBody>
              </Table>
            </TooltipProvider>
          )}
        </CardContent>
      </Card>

      <SetSecretSheet
        open={setOpen}
        onOpenChange={setSetOpen}
        slug={slug}
        onSubmit={async (key, value) => {
          const { data } = await setSecret({
            variables: {
              input: {
                appSlug: slug,
                key,
                value,
                ifMatchVersion: appVersion.data?.astroliftApp?.version ?? null,
              },
            },
          });
          if (data?.setAppSecret.ok) {
            toast.success(`Set ${key}`);
            setSetOpen(false);
            return true;
          }
          if (
            handleVersionMismatch(data?.setAppSecret, {
              label: "app",
              onRefresh: () => appVersion.refetch(),
            })
          ) {
            return false;
          }
          toast.error(data?.setAppSecret.errors?.[0]?.message ?? "Save failed");
          return false;
        }}
        busy={busy}
      />

      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(next) => {
          if (!next) setDeleteTarget(null);
        }}
        title={
          deleteTarget ? t("delete.title", { key: deleteTarget.key }) : t("delete.fallbackTitle")
        }
        description={t("delete.description")}
        confirmLabel={t("delete.confirm")}
        destructive
        onConfirm={async () => {
          if (deleteTarget) await handleDelete(deleteTarget);
        }}
      />

      <ConfirmDialog
        open={detachTarget !== null}
        onOpenChange={(next) => {
          if (!next) setDetachTarget(null);
        }}
        title={
          detachTarget
            ? t("attached.detachConfirmTitle", {
                name: detachTarget.bundleName,
              })
            : ""
        }
        description={
          detachTarget
            ? t("attached.detachConfirmDescription", {
                env: detachTarget.environmentName,
              })
            : ""
        }
        confirmLabel={t("attached.detachConfirm")}
        destructive
        onConfirm={async () => {
          if (detachTarget) await handleDetach(detachTarget);
        }}
      />

      <BulkImportSheet
        open={bulkOpen}
        onOpenChange={setBulkOpen}
        onSubmit={async (dotenvText) => {
          const { data } = await bulkImport({
            variables: { input: { appSlug: slug, dotenvText } },
          });
          if (data?.bulkImportAppSecrets.ok) {
            const keys = data.bulkImportAppSecrets.data?.keysSet ?? [];
            toast.success(`Imported ${keys.length} key${keys.length === 1 ? "" : "s"}`);
            setBulkOpen(false);
            return true;
          }
          toast.error(data?.bulkImportAppSecrets.errors?.[0]?.message ?? "Import failed");
          return false;
        }}
        busy={busy}
      />
    </PageShell>
  );
}

interface SecretRowProps {
  secret: AppSecret;
  revealed: string | null;
  editing: boolean;
  busy: boolean;
  revealing: boolean;
  onToggleReveal: () => void;
  onStartEdit: () => void;
  onCancelEdit: () => void;
  onSave: (value: string) => Promise<boolean>;
  onRequestDelete: () => void;
}

function SecretRow({
  secret: s,
  revealed,
  editing,
  busy,
  revealing,
  onToggleReveal,
  onStartEdit,
  onCancelEdit,
  onSave,
  onRequestDelete,
}: SecretRowProps) {
  const t = useTranslations("apps.secrets");
  const isRevealed = revealed !== null;
  const canEdit = s.source === "literal" && isRevealed;
  const lastEditedTooltip = s.lastEditedBy
    ? t("lastEditedBy", {
        name: s.lastEditedBy.displayName || s.lastEditedBy.username,
      })
    : t("lastEditedByUnknown");

  return (
    <TableRow>
      <TableCell className="font-mono text-xs">{s.key}</TableCell>
      <TableCell className="font-mono text-xs">
        {editing && canEdit ? (
          <InlineValueEditor
            initial={revealed ?? ""}
            busy={busy}
            onSave={onSave}
            onCancel={onCancelEdit}
          />
        ) : isRevealed ? (
          <ValueRevealed
            value={revealed!}
            canEdit={s.source === "literal"}
            onStartEdit={onStartEdit}
          />
        ) : (
          <span className="text-muted-foreground select-none">••••••••</span>
        )}
      </TableCell>
      <TableCell>
        <Badge variant={SOURCE_TONE[s.source] ?? "outline"}>
          {SOURCE_LABEL[s.source] ?? s.source}
        </Badge>
        {s.bundleSlug && (
          <span className="text-muted-foreground ml-2 font-mono text-[11px]">{s.bundleSlug}</span>
        )}
        {s.managedServiceKind && (
          <span className="text-muted-foreground ml-2 font-mono text-[11px]">
            {s.managedServiceKind}
          </span>
        )}
      </TableCell>
      <TableCell>
        <Badge variant="outline" className="font-mono text-[10px]">
          {s.environmentName || "—"}
        </Badge>
      </TableCell>
      <TableCell className="text-muted-foreground text-xs">
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="cursor-help">
              {s.lastEditedAt ? new Date(s.lastEditedAt).toLocaleString() : "—"}
            </span>
          </TooltipTrigger>
          <TooltipContent>{lastEditedTooltip}</TooltipContent>
        </Tooltip>
      </TableCell>
      <TableCell className="text-right">
        <div className="inline-flex items-center gap-1">
          {s.source === "literal" && (
            <Can permission="secret.read">
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-8"
                    onClick={onToggleReveal}
                    disabled={busy || revealing}
                    aria-pressed={isRevealed}
                  >
                    {revealing ? (
                      <Loader2Icon className="size-4 animate-spin" />
                    ) : isRevealed ? (
                      <EyeOffIcon className="size-4" />
                    ) : (
                      <EyeIcon className="size-4" />
                    )}
                    <span className="sr-only">
                      {isRevealed ? t("reveal.hide") : t("reveal.show")}
                    </span>
                  </Button>
                </TooltipTrigger>
                <TooltipContent>{isRevealed ? t("reveal.hide") : t("reveal.show")}</TooltipContent>
              </Tooltip>
            </Can>
          )}
          {s.source === "literal" && (
            <Can permission="app.deploy">
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-8"
                    onClick={onRequestDelete}
                    disabled={busy}
                  >
                    <Trash2Icon className="size-4" />
                    <span className="sr-only">Delete</span>
                  </Button>
                </TooltipTrigger>
                <TooltipContent>{t("delete.confirm")}</TooltipContent>
              </Tooltip>
            </Can>
          )}
        </div>
      </TableCell>
    </TableRow>
  );
}

function ValueRevealed({
  value,
  canEdit,
  onStartEdit,
}: {
  value: string;
  canEdit: boolean;
  onStartEdit: () => void;
}) {
  const t = useTranslations("apps.secrets");
  if (canEdit) {
    return (
      <Can permission="app.deploy" fallback={<span className="break-all">{value}</span>}>
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              onClick={onStartEdit}
              className="group hover:bg-muted/40 inline-flex max-w-full items-center gap-1 rounded px-1 py-0.5 text-left"
            >
              <span className="break-all">{value}</span>
              <PencilIcon className="text-muted-foreground size-3 opacity-0 transition-opacity group-hover:opacity-100" />
            </button>
          </TooltipTrigger>
          <TooltipContent>{t("edit.save")}</TooltipContent>
        </Tooltip>
      </Can>
    );
  }
  return <span className="break-all">{value}</span>;
}

function InlineValueEditor({
  initial,
  busy,
  onSave,
  onCancel,
}: {
  initial: string;
  busy: boolean;
  onSave: (value: string) => Promise<boolean>;
  onCancel: () => void;
}) {
  const t = useTranslations("apps.secrets");
  const [value, setValue] = React.useState(initial);
  const [saving, setSaving] = React.useState(false);
  const inputRef = React.useRef<HTMLInputElement | null>(null);

  React.useEffect(() => {
    inputRef.current?.focus();
    inputRef.current?.select();
  }, []);

  async function commit() {
    if (saving || busy) return;
    setSaving(true);
    try {
      await onSave(value);
    } finally {
      setSaving(false);
    }
  }

  return (
    <form
      onSubmit={async (e) => {
        e.preventDefault();
        await commit();
      }}
      className="flex items-center gap-1"
    >
      <Input
        ref={inputRef}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            e.preventDefault();
            onCancel();
          }
        }}
        className="h-8 font-mono text-xs"
        spellCheck={false}
        autoComplete="off"
        disabled={saving}
      />
      <Button
        type="submit"
        size="icon"
        variant="ghost"
        className="size-8"
        disabled={saving || busy}
        aria-label={t("edit.save")}
      >
        {saving ? (
          <Loader2Icon className="size-4 animate-spin" />
        ) : (
          <CheckIcon className="size-4" />
        )}
      </Button>
      <Button
        type="button"
        size="icon"
        variant="ghost"
        className="size-8"
        onClick={onCancel}
        disabled={saving}
        aria-label={t("edit.cancel")}
      >
        <XIcon className="size-4" />
      </Button>
      <span className="text-muted-foreground hidden text-[10px] sm:inline">{t("edit.hint")}</span>
    </form>
  );
}

function AttachedBundlesSection({
  loading,
  attachments,
  onDetach,
  busy,
}: {
  loading: boolean;
  attachments: AppSecretBundleAttachment[];
  onDetach: (a: AppSecretBundleAttachment) => void;
  busy: boolean;
}) {
  const t = useTranslations("apps.secrets.attached");

  return (
    <Card>
      <CardContent className="space-y-3 p-4">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 className="flex items-center gap-2 text-sm font-semibold">
              <LayersIcon className="size-4" />
              {t("title")}
            </h2>
            <p className="text-muted-foreground mt-0.5 text-xs">{t("description")}</p>
          </div>
        </div>

        {loading ? (
          <div className="space-y-2">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : attachments.length === 0 ? (
          <p className="text-muted-foreground text-xs">{t("empty")}</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t("columns.bundle")}</TableHead>
                <TableHead>{t("columns.team")}</TableHead>
                <TableHead>{t("columns.env")}</TableHead>
                <TableHead>{t("columns.prefix")}</TableHead>
                <TableHead>{t("columns.keys")}</TableHead>
                <TableHead>{t("columns.order")}</TableHead>
                <TableHead>{t("columns.attached")}</TableHead>
                <TableHead className="w-12 text-right" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {attachments.map((a) => (
                <TableRow key={a.id}>
                  <TableCell className="font-mono text-xs">
                    {a.bundleName}
                    <span className="text-muted-foreground ml-2 text-[10px]">{a.bundleSlug}</span>
                  </TableCell>
                  <TableCell>
                    {a.teamSlug ? (
                      <Badge variant="outline" className="font-mono text-[10px]">
                        {a.teamSlug}
                      </Badge>
                    ) : (
                      <span className="text-muted-foreground text-xs">{t("noTeam")}</span>
                    )}
                  </TableCell>
                  <TableCell>
                    <Badge variant="outline" className="font-mono text-[10px]">
                      {t("perEnvBadge", { env: a.environmentName })}
                    </Badge>
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {a.prefix ? (
                      a.prefix
                    ) : (
                      <span className="text-muted-foreground">{t("noPrefix")}</span>
                    )}
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {a.keyCount > 0 ? a.keyCount : t("keyCountUnknown")}
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    <TooltipProvider delayDuration={200}>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Badge variant="secondary" className="font-mono">
                            {a.mergeOrder}
                          </Badge>
                        </TooltipTrigger>
                        <TooltipContent>{t("orderHint")}</TooltipContent>
                      </Tooltip>
                    </TooltipProvider>
                  </TableCell>
                  <TableCell className="text-muted-foreground text-xs">
                    {a.attachedAt ? new Date(a.attachedAt).toLocaleString() : "—"}
                  </TableCell>
                  <TableCell className="text-right">
                    <Can permission="app.deploy">
                      <Button variant="ghost" size="sm" onClick={() => onDetach(a)} disabled={busy}>
                        {t("detach")}
                      </Button>
                    </Can>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}

function SetSecretSheet({
  open,
  onOpenChange,
  slug: _slug,
  onSubmit,
  busy,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  slug: string;
  onSubmit: (key: string, value: string) => Promise<boolean>;
  busy: boolean;
}) {
  const t = useTranslations("apps.secrets.setSheet");
  const tCommon = useTranslations("apps.common");
  const [key, setKey] = React.useState("");
  const [value, setValue] = React.useState("");

  React.useEffect(() => {
    if (!open) {
      setKey("");
      setValue("");
    }
  }, [open]);

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex flex-col">
        <SheetHeader>
          <SheetTitle>{t("title")}</SheetTitle>
          <SheetDescription>{t("description")}</SheetDescription>
        </SheetHeader>
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            if (!key.trim() || !value) return;
            await onSubmit(key.trim(), value);
          }}
          className="flex flex-1 flex-col gap-4 px-4 pb-4"
        >
          <div className="space-y-2">
            <Label htmlFor="secret-key">{t("keyLabel")}</Label>
            <Input
              id="secret-key"
              value={key}
              onChange={(e) => setKey(e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, "_"))}
              placeholder="DATABASE_URL"
              autoFocus
              required
              spellCheck={false}
              className="font-mono"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="secret-value">{t("valueLabel")}</Label>
            <Input
              id="secret-value"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              type="password"
              required
              spellCheck={false}
              className="font-mono"
            />
            <p className="text-muted-foreground text-xs">{t("valueHint")}</p>
          </div>
          <SheetFooter className="mt-auto flex-row justify-end gap-2 px-0">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              {tCommon("cancel")}
            </Button>
            <Button type="submit" disabled={busy || !key || !value}>
              {busy ? t("submitting") : t("submit")}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}

function BulkImportSheet({
  open,
  onOpenChange,
  onSubmit,
  busy,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (dotenvText: string) => Promise<boolean>;
  busy: boolean;
}) {
  const t = useTranslations("apps.secrets.bulkSheet");
  const tCommon = useTranslations("apps.common");
  const [text, setText] = React.useState("");

  React.useEffect(() => {
    if (!open) setText("");
  }, [open]);

  const previewKeys = React.useMemo(() => {
    return text
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => line && !line.startsWith("#"))
      .map((line) => line.split("=")[0]?.trim())
      .filter((k): k is string => Boolean(k));
  }, [text]);

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex flex-col sm:max-w-xl">
        <SheetHeader>
          <SheetTitle>{t("title")}</SheetTitle>
          <SheetDescription>{t("description")}</SheetDescription>
        </SheetHeader>
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            if (!text.trim()) return;
            await onSubmit(text);
          }}
          className="flex flex-1 flex-col gap-4 px-4 pb-4"
        >
          <Textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={16}
            spellCheck={false}
            className="font-mono text-xs"
            placeholder={"DATABASE_URL=postgres://...\nREDIS_URL=redis://..."}
          />
          {previewKeys.length > 0 && (
            <div className="bg-muted/30 rounded border p-3 text-xs">
              <p className="text-muted-foreground mb-2">
                {t("willSet", { count: previewKeys.length })}
              </p>
              <div className="flex flex-wrap gap-1">
                {previewKeys.slice(0, 20).map((k) => (
                  <Badge key={k} variant="outline" className="font-mono text-[10px]">
                    {k}
                  </Badge>
                ))}
                {previewKeys.length > 20 && (
                  <Badge variant="outline" className="font-mono text-[10px]">
                    +{previewKeys.length - 20}
                  </Badge>
                )}
              </div>
            </div>
          )}
          <SheetFooter className="mt-auto flex-row justify-end gap-2 px-0">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              {tCommon("cancel")}
            </Button>
            <Button type="submit" disabled={busy || !text.trim()}>
              {busy ? t("submitting") : t("submit")}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}

// #488 — secret-change proposal banner. Rendered above the secrets
// table when the app has pending proposals. Each proposal links into
// the global proposal-detail page; from there the approver can
// approve / reject. Banner is ambient context only — primary action
// surface is the /approvals queue.
function PendingProposalsBanner({ proposals }: { proposals: AstroliftSecretChangeProposal[] }) {
  const t = useTranslations("apps.secrets.pendingProposals");
  if (proposals.length === 0) {
    return null;
  }
  return (
    <div className="bg-card rounded-md border p-3 text-sm">
      <p className="font-medium">{t("title", { count: proposals.length })}</p>
      <ul className="mt-2 flex flex-col gap-1">
        {proposals.slice(0, 5).map((proposal) => {
          const summary =
            (proposal.payloadDiff as { summary?: string })?.summary ??
            `${proposal.op} on ${proposal.environmentName || t("appWide")}`;
          return (
            <li key={proposal.id} className="text-muted-foreground text-xs">
              <Link
                className="text-foreground hover:underline"
                href={`/approvals/secret/${proposal.id}`}
              >
                {summary}
              </Link>
              <span className="ml-2">
                {t("votes", {
                  received: proposal.approvalsCount,
                  required: proposal.requiredApproverCount,
                })}
              </span>
            </li>
          );
        })}
        {proposals.length > 5 && (
          <li className="text-muted-foreground text-xs">
            <Link className="hover:underline" href="/approvals">
              {t("seeAll", { count: proposals.length })}
            </Link>
          </li>
        )}
      </ul>
    </div>
  );
}
