"use client";

import {
  CheckIcon,
  EyeIcon,
  EyeOffIcon,
  HistoryIcon,
  KeyIcon,
  LayersIcon,
  Loader2Icon,
  PencilIcon,
  PlusIcon,
  RotateCwIcon,
  Trash2Icon,
  UploadIcon,
  XIcon,
} from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";

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
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
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
import type { AstroliftSecretChangeProposal } from "@/graphql/services/services.types";

import type { AppSecret, AppSecretBundleAttachment } from "./secrets.types";
import { ALL_ENVS, scopeBadgeLabel, type useAppSecrets } from "./use-app-secrets";

export type SecretsScreenProps = ReturnType<typeof useAppSecrets> & {
  /** The app tab bar. */
  tabs: React.ReactNode;
  /** The "Push to GitHub" (push & rotate) toolbar button; it has data of its own. */
  pushToGitHub: React.ReactNode;
  /** The audit history popover body for one key; runs its query only while open. */
  renderHistory: (secretKey: string) => React.ReactNode;
};

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

// #678 — provenance of the most recent set/rotate write. Keep these in
// sync with `AppSecretType.set_via` valid values in the backend.
const SET_VIA_LABEL: Record<string, string> = {
  web: "web",
  cli: "CLI",
  env_paste: ".env paste",
  bundle: "bundle sync",
  managed_service: "managed service",
};

// #679 — per-environment scope sentinel for the "preview:<branch>"
// option in the scope <Select>. The literal value is replaced with
// "preview:" + branch input when the form is submitted; the sentinel
// is never sent to the server.
const SCOPE_PREVIEW_BRANCH = "preview:<branch>";

/** #679 — inline scope chip next to the secret key cell. Hidden for
 *  the default "all" scope so the table stays quiet for the common
 *  case; renders for production / preview / preview:branch. */
function SecretScopeBadge({ scope }: { scope: string | null | undefined }) {
  if (!scope || scope === "all") return null;
  const isProd = scope === "production";
  return (
    <Badge
      variant="outline"
      className={
        isProd
          ? "border-warning-border bg-warning/10 text-2xs text-warning-fg ml-2"
          : "border-info-border bg-info/10 text-2xs text-info-fg ml-2"
      }
    >
      {scopeBadgeLabel(scope)}
    </Badge>
  );
}

/** #677 — small inline expiry chip for the secret key cell. Hidden when
 *  no rotation deadline is set; warning < 14d; destructive < 7d / past. */
function SecretExpiryBadge({ expiresAt }: { expiresAt: string | null | undefined }) {
  const [now] = React.useState(() => Date.now());
  if (!expiresAt) return null;
  const ms = new Date(expiresAt).getTime() - now;
  const days = Math.floor(ms / (1000 * 60 * 60 * 24));
  if (days < 0) {
    return (
      <Badge variant="destructive" className="text-2xs ml-2">
        Expired {Math.abs(days)}d ago
      </Badge>
    );
  }
  if (days <= 7) {
    return (
      <Badge variant="destructive" className="text-2xs ml-2">
        Rotate — expires in {days}d
      </Badge>
    );
  }
  if (days <= 14) {
    return (
      <Badge
        variant="outline"
        className="border-warning-border bg-warning/10 text-2xs text-warning-fg ml-2"
      >
        Expires in {days}d
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className="text-muted-foreground text-2xs ml-2">
      Expires in {days}d
    </Badge>
  );
}

/** #1923 — flags a literal row whose staged value has no matching applied
 *  proposal: what shows here is not what the next deploy actually ships.
 *  Covers both a normal pending review and a value staged while approval
 *  was briefly off, since either way the next deploy reverts it. */
function SecretPendingApprovalBadge({
  source,
  deploysAsShown,
}: {
  source: string;
  deploysAsShown?: boolean | null;
}) {
  const t = useTranslations("apps.secrets");
  if (source !== "literal" || deploysAsShown !== false) return null;
  return (
    <Badge
      variant="outline"
      className="border-warning-border bg-warning/10 text-2xs text-warning-fg ml-2"
      title={t("pendingApprovalTooltip")}
    >
      {t("pendingApprovalBadge")}
    </Badge>
  );
}

/** #1923 — surfaces literal keys that will revert to their last-approved
 *  value on the next deploy, ahead of time: staged with no matching
 *  applied proposal, whether mid-review under an always-on approval
 *  policy or staged during a window when approval was briefly off.
 *  Deduplicated by key -- deploysAsShown is an app-wide answer, so the
 *  same key repeats across every environment row it appears in. */
function RevertWarningBanner({ secrets }: { secrets: AppSecret[] }) {
  const t = useTranslations("apps.secrets");
  const keys = Array.from(
    new Set(
      secrets.filter((s) => s.source === "literal" && s.deploysAsShown === false).map((s) => s.key)
    )
  ).sort();
  if (keys.length === 0) return null;
  return (
    <div className="bg-card border-warning-border rounded-md border p-3 text-sm">
      <p className="font-medium">{t("revertWarning.title", { count: keys.length })}</p>
      <p className="text-muted-foreground mt-1 text-xs">{t("revertWarning.description")}</p>
      <ul className="mt-2 flex flex-wrap gap-x-3 gap-y-1">
        {keys.slice(0, 8).map((key) => (
          <li key={key} className="font-mono text-xs">
            {key}
          </li>
        ))}
        {keys.length > 8 && (
          <li className="text-muted-foreground text-xs">
            {t("revertWarning.seeAll", { count: keys.length - 8 })}
          </li>
        )}
      </ul>
    </div>
  );
}

/**
 * The app Secrets tab. Holds only UI state (which sheet, confirm, or history
 * popover is open); everything that talks to the server comes in from
 * useAppSecrets.
 */
export function SecretsScreen({
  slug,
  envName,
  setEnvName,
  environments: envList,
  secrets: list,
  secretsLoading,
  attachments: attachmentList,
  attachmentsLoading,
  pendingProposals,
  revealedValues,
  editingId,
  rotatingId,
  revealingId,
  busy,
  rotating,
  detaching,
  onToggleReveal,
  onStartEdit,
  onStartRotate,
  onCancelEdit,
  onInlineSave,
  onRequestDelete,
  onDelete,
  onDetach,
  onSetSecret,
  onBulkImport,
  tabs,
  pushToGitHub,
  renderHistory,
}: SecretsScreenProps) {
  const t = useTranslations("apps.secrets");
  const [setOpen, setSetOpen] = React.useState(false);
  const [bulkOpen, setBulkOpen] = React.useState(false);
  const [deleteTarget, setDeleteTarget] = React.useState<AppSecret | null>(null);
  const [detachTarget, setDetachTarget] = React.useState<AppSecretBundleAttachment | null>(null);
  // #714 — historyKey: which (id, key) pair's audit popover is open.
  const [historyTarget, setHistoryTarget] = React.useState<{
    secretId: string;
    key: string;
  } | null>(null);

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
          {/* #681 — mirror the Settings tab's "Push & rotate" affordance
              here, where an operator's mental model says "I'm working
              with secrets, that belongs in this view". The Settings card
              wraps the same button with an explanatory blurb; the
              toolbar surfaces the action without the prose since the
              operator already knows what it does by the time they hit
              this page. */}
          <Can permission="app.deploy">{pushToGitHub}</Can>
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
      {tabs}

      <PendingProposalsBanner proposals={pendingProposals} />
      <RevertWarningBanner secrets={list} />

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
        loading={attachmentsLoading}
        attachments={attachmentList}
        onDetach={(a) => setDetachTarget(a)}
        busy={detaching}
      />

      <Card>
        <CardContent className="p-0">
          {secretsLoading ? (
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
                      editing={editingId === s.id || rotatingId === s.id}
                      isRotateMode={rotatingId === s.id}
                      onToggleReveal={() => onToggleReveal(s)}
                      onStartEdit={() => onStartEdit(s)}
                      onStartRotate={() => onStartRotate(s)}
                      onCancelEdit={onCancelEdit}
                      onSave={(value) => onInlineSave(s, value)}
                      onRequestDelete={() => {
                        if (onRequestDelete(s)) setDeleteTarget(s);
                      }}
                      historyOpen={historyTarget?.secretId === s.id}
                      history={historyTarget?.secretId === s.id ? renderHistory(s.key) : null}
                      onToggleHistory={() =>
                        setHistoryTarget((prev) =>
                          prev?.secretId === s.id ? null : { secretId: s.id, key: s.key }
                        )
                      }
                      busy={busy || rotating}
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
        onSubmit={async (key, value, scope) => {
          const ok = await onSetSecret(key, value, scope);
          if (ok) setSetOpen(false);
          return ok;
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
          if (deleteTarget) await onDelete(deleteTarget);
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
          if (detachTarget) await onDetach(detachTarget);
        }}
      />

      <BulkImportSheet
        open={bulkOpen}
        onOpenChange={setBulkOpen}
        onSubmit={async (dotenvText) => {
          const ok = await onBulkImport(dotenvText);
          if (ok) setBulkOpen(false);
          return ok;
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
  isRotateMode: boolean;
  busy: boolean;
  revealing: boolean;
  historyOpen: boolean;
  /** The audit history popover body, rendered only while it is open. */
  history: React.ReactNode;
  onToggleReveal: () => void;
  onStartEdit: () => void;
  onStartRotate: () => void;
  onCancelEdit: () => void;
  onSave: (value: string) => Promise<boolean>;
  onRequestDelete: () => void;
  onToggleHistory: () => void;
}

function SecretRow({
  secret: s,
  history,
  revealed,
  editing,
  isRotateMode,
  busy,
  revealing,
  historyOpen,
  onToggleReveal,
  onStartEdit,
  onStartRotate,
  onCancelEdit,
  onSave,
  onRequestDelete,
  onToggleHistory,
}: SecretRowProps) {
  const t = useTranslations("apps.secrets");
  const isRevealed = revealed !== null;
  const canEdit = s.source === "literal" && isRevealed;
  const editorName = s.lastEditedBy?.displayName || s.lastEditedBy?.username;
  const setViaLabel = SET_VIA_LABEL[s.setVia ?? "web"] ?? s.setVia ?? "web";
  // Build a multi-line tooltip: "edited by X — set via Y". Drops the
  // "by X" half when the writer is unknown (e.g. backfilled rows).
  const lastEditedTooltip = editorName
    ? `${t("lastEditedBy", { name: editorName })} · set via ${setViaLabel}`
    : `${t("lastEditedByUnknown")} · set via ${setViaLabel}`;

  return (
    <TableRow>
      <TableCell className="font-mono text-xs">
        {s.key}
        <SecretScopeBadge scope={s.scope} />
        <SecretExpiryBadge expiresAt={s.expiresAt} />
        <SecretPendingApprovalBadge source={s.source} deploysAsShown={s.deploysAsShown} />
      </TableCell>
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
          <span className="text-muted-foreground text-2xs ml-2 font-mono">{s.bundleSlug}</span>
        )}
        {s.managedServiceKind && (
          <span className="text-muted-foreground text-2xs ml-2 font-mono">
            {s.managedServiceKind}
          </span>
        )}
      </TableCell>
      <TableCell>
        <Badge variant="outline" className="text-2xs font-mono">
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
          {/* #714 — Rotate button. Same flow as Edit (opens
              InlineValueEditor) but routes save → rotateAppSecret so
              the audit log carries action='app.secret.rotate'. Only
              shown for revealed literal secrets to match Edit's
              precondition. */}
          {canEdit && (
            <Can permission="app.deploy">
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-8"
                    onClick={onStartRotate}
                    disabled={busy || editing}
                    aria-pressed={isRotateMode}
                  >
                    <RotateCwIcon className="size-4" />
                    <span className="sr-only">Rotate</span>
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Rotate (distinct from edit in the audit log)</TooltipContent>
              </Tooltip>
            </Can>
          )}
          {/* #714 — History popover. Lazily fetches the per-key audit
              timeline on open; renders newest-first list with actor,
              action, timestamp. */}
          {s.source === "literal" && (
            <Can permission="secret.read">
              <Popover open={historyOpen} onOpenChange={onToggleHistory}>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <PopoverTrigger asChild>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="size-8"
                        aria-pressed={historyOpen}
                      >
                        <HistoryIcon className="size-4" />
                        <span className="sr-only">History</span>
                      </Button>
                    </PopoverTrigger>
                  </TooltipTrigger>
                  <TooltipContent>Audit history for this key</TooltipContent>
                </Tooltip>
                <PopoverContent align="end" className="w-96 p-0">
                  {historyOpen && history}
                </PopoverContent>
              </Popover>
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
      <span className="text-muted-foreground text-2xs hidden sm:inline">{t("edit.hint")}</span>
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
                    <span className="text-muted-foreground text-2xs ml-2">{a.bundleSlug}</span>
                  </TableCell>
                  <TableCell>
                    {a.teamSlug ? (
                      <Badge variant="outline" className="text-2xs font-mono">
                        {a.teamSlug}
                      </Badge>
                    ) : (
                      <span className="text-muted-foreground text-xs">{t("noTeam")}</span>
                    )}
                  </TableCell>
                  <TableCell>
                    <Badge variant="outline" className="text-2xs font-mono">
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
  onSubmit,
  busy,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (key: string, value: string, scope: string) => Promise<boolean>;
  busy: boolean;
}) {
  const t = useTranslations("apps.secrets.setSheet");
  const tCommon = useTranslations("apps.common");
  const [key, setKey] = React.useState("");
  const [value, setValue] = React.useState("");
  // #679 — scope selector. "all" (default) | "production" | "preview"
  // | SCOPE_PREVIEW_BRANCH (sentinel → "preview:<branch>" on submit).
  const [scope, setScope] = React.useState<string>("all");
  const [previewBranch, setPreviewBranch] = React.useState("");

  React.useEffect(() => {
    if (!open) {
      setKey("");
      setValue("");
      setScope("all");
      setPreviewBranch("");
    }
  }, [open]);

  const needsBranchInput = scope === SCOPE_PREVIEW_BRANCH;
  const effectiveScope = needsBranchInput ? `preview:${previewBranch.trim()}` : scope;
  const scopeReady = !needsBranchInput || previewBranch.trim().length > 0;

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
            if (!key.trim() || !value || !scopeReady) return;
            await onSubmit(key.trim(), value, effectiveScope);
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
          {/* #679 — scope selector. Default "all" matches the historic
              behavior; production / preview / preview:<branch> let
              operators carve preview branches off from prod values. */}
          <div className="space-y-2">
            <Label htmlFor="secret-scope">Scope</Label>
            <Select value={scope} onValueChange={setScope}>
              <SelectTrigger id="secret-scope">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All environments (default)</SelectItem>
                <SelectItem value="production">Production only</SelectItem>
                <SelectItem value="preview">All previews</SelectItem>
                <SelectItem value={SCOPE_PREVIEW_BRANCH}>Specific preview branch…</SelectItem>
              </SelectContent>
            </Select>
            {needsBranchInput && (
              <Input
                id="secret-scope-branch"
                value={previewBranch}
                onChange={(e) => setPreviewBranch(e.target.value)}
                placeholder="feature/login-redesign"
                spellCheck={false}
                className="font-mono"
                required
              />
            )}
            <p className="text-muted-foreground text-xs">
              {scope === "all"
                ? "Visible to every deploy of this app."
                : scope === "production"
                  ? "Only production deploys can read this value."
                  : scope === "preview"
                    ? "Only preview deploys can read this value."
                    : "Only the named preview branch can read this value."}
            </p>
          </div>
          <SheetFooter className="mt-auto flex-row justify-end gap-2 px-0">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              {tCommon("cancel")}
            </Button>
            <Button type="submit" disabled={busy || !key || !value || !scopeReady}>
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
                  <Badge key={k} variant="outline" className="text-2xs font-mono">
                    {k}
                  </Badge>
                ))}
                {previewKeys.length > 20 && (
                  <Badge variant="outline" className="text-2xs font-mono">
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
