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

import { useFormatters } from "@/lib/i18n/formatters";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import type { Column } from "@/components/data-table";
import { DetailTabSections } from "@/components/detail/DetailTabSections";
import { ListPage } from "@/components/list/ListPage";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
import { Textarea } from "@/components/ui/textarea";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import type { ListStateController } from "@/components/list/list-state";
import type { AstroliftSecretChangeProposal } from "@/graphql/services/services.types";

import { SECRETS_SECTIONS, type SecretsSection } from "./secrets-list";
import type { AppSecret, AppSecretBundleAttachment } from "./secrets.types";
import { ALL_ENVS, useSecretScopeLabel, type useAppSecrets } from "./use-app-secrets";

export type SecretsScreenProps = ReturnType<typeof useAppSecrets> & {
  /** The app tab bar. */
  tabs: React.ReactNode;
  /** The "Push to GitHub" (push & rotate) toolbar button; it has data of its own. */
  pushToGitHub: React.ReactNode;
  /** The audit history popover body for one key; runs its query only while open. */
  renderHistory: (secretKey: string) => React.ReactNode;
  /** A section's link: the tab's route, with `?section=bundles` for the bundles. */
  sectionHref: (section: SecretsSection) => string;
};

const SOURCE_TONE: Record<string, "secondary" | "outline" | "default"> = {
  literal: "secondary",
  bundle: "outline",
  managed_service: "default",
};

// #678 — provenance of the most recent set/rotate write. Keep these in
// sync with `AppSecretType.set_via` valid values in the backend.
// #679 — per-environment scope sentinel for the "preview:<branch>"
// option in the scope <Select>. The literal value is replaced with
// "preview:" + branch input when the form is submitted; the sentinel
// is never sent to the server.
const SCOPE_PREVIEW_BRANCH = "preview:<branch>";

/** #679 — inline scope chip next to the secret key cell. Hidden for
 *  the default "all" scope so the table stays quiet for the common
 *  case; renders for production / preview / preview:branch. */
function SecretScopeBadge({ scope }: { scope: string | null | undefined }) {
  const scopeLabel = useSecretScopeLabel();
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
      {scopeLabel(scope)}
    </Badge>
  );
}

/** #677 — small inline expiry chip for the secret key cell. Hidden when
 *  no rotation deadline is set; warning < 14d; destructive < 7d / past. */
function SecretExpiryBadge({ expiresAt }: { expiresAt: string | null | undefined }) {
  const t = useTranslations("apps.secrets.expiry");
  const [now] = React.useState(() => Date.now());
  if (!expiresAt) return null;
  const ms = new Date(expiresAt).getTime() - now;
  const days = Math.floor(ms / (1000 * 60 * 60 * 24));
  if (!Number.isFinite(days)) return null;
  if (days < 0) {
    return (
      <Badge variant="destructive" className="text-2xs ml-2">
        {t("expired", { days: Math.abs(days) })}
      </Badge>
    );
  }
  if (days <= 7) {
    return (
      <Badge variant="destructive" className="text-2xs ml-2">
        {t("rotateSoon", { days })}
      </Badge>
    );
  }
  if (days <= 14) {
    return (
      <Badge
        variant="outline"
        className="border-warning-border bg-warning/10 text-2xs text-warning-fg ml-2"
      >
        {t("expires", { days })}
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className="text-muted-foreground text-2xs ml-2">
      {t("expires", { days })}
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
 * The app Secrets tab (spec 44 §5.1, §5.2, §5.4), one list per section
 * (Leo's list rule 3): Secrets, the keys on the embedded list, in mono and
 * masked until revealed (reveal needs `secret.read`, as before), a key's
 * audit history in a side sheet, and New secret as a sheet of three fields;
 * and Attached bundles (`?section=bundles`) on a list of its own. The
 * environment picker scopes both. Holds only UI state (which sheet, confirm
 * or history is open); everything that talks to the server comes in from
 * useAppSecrets.
 */
export function SecretsScreen({
  slug,
  section,
  envName,
  setEnvName,
  environments: envList,
  secrets: list,
  keysList,
  keyRows,
  keyTotal,
  secretsLoading,
  secretsError,
  retrySecrets,
  bundlesList,
  bundleRows,
  bundleTotal,
  attachmentsLoading,
  attachmentsError,
  retryAttachments,
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
  sectionHref,
}: SecretsScreenProps) {
  const t = useTranslations("apps.secrets");
  const fmt = useFormatters();
  const [setOpen, setSetOpen] = React.useState(false);
  const [bulkOpen, setBulkOpen] = React.useState(false);
  const [deleteTarget, setDeleteTarget] = React.useState<AppSecret | null>(null);
  const [detachTarget, setDetachTarget] = React.useState<AppSecretBundleAttachment | null>(null);
  // #714: whose audit history is open in the side sheet.
  const [historyTarget, setHistoryTarget] = React.useState<AppSecret | null>(null);

  const columns = secretColumns({
    t,
    formatDateTime: fmt.formatDateTime,
    revealedValues,
    editingId,
    rotatingId,
    revealingId,
    historyId: historyTarget?.id ?? null,
    busy: busy || rotating,
    onToggleReveal,
    onStartEdit,
    onStartRotate,
    onCancelEdit,
    onInlineSave,
    onRequestDelete: (s) => {
      if (onRequestDelete(s)) setDeleteTarget(s);
    },
    onOpenHistory: setHistoryTarget,
  });

  return (
    <PageShell
      title={t("title")}
      description={
        <span className="font-mono text-xs [overflow-wrap:anywhere]">
          {t("description", { slug })}
        </span>
      }
      actions={
        <>
          {/* #681: the Settings tab's "Push & rotate", here too, where the
              operator is already working with secrets. */}
          <Select value={envName} onValueChange={setEnvName}>
            <SelectTrigger
              size="sm"
              aria-label={t("environment")}
              className="w-44 max-w-full min-w-0 font-mono text-xs"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL_ENVS}>{t("allEnvironments")}</SelectItem>
              {envList.map((e) => (
                <SelectItem key={e.id} value={e.name} className="font-mono">
                  {e.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Can permission="app.deploy">{pushToGitHub}</Can>
          <Can permission="app.deploy">
            <Button size="sm" variant="outline" onClick={() => setBulkOpen(true)}>
              <UploadIcon className="size-4" />
              {t("bulkImport")}
            </Button>
          </Can>
          <Can permission="app.deploy">
            <Button size="sm" onClick={() => setSetOpen(true)}>
              <PlusIcon className="size-4" />
              {t("newSecret")}
            </Button>
          </Can>
        </>
      }
    >
      {tabs}

      <DetailTabSections
        ariaLabel={t("title")}
        sections={SECRETS_SECTIONS.map((id) => ({
          id,
          label: id === "bundles" ? t("attached.title") : t("title"),
          href: sectionHref(id),
        }))}
        active={section}
      >
        {section === "bundles" ? (
          <AttachedBundlesList
            list={bundlesList}
            rows={bundleRows}
            totalCount={bundleTotal}
            loading={attachmentsLoading}
            error={attachmentsError}
            onRetry={retryAttachments}
            onDetach={(a) => setDetachTarget(a)}
            busy={detaching}
          />
        ) : (
          <>
            <PendingProposalsBanner proposals={pendingProposals} />
            <RevertWarningBanner secrets={list} />
            <TooltipProvider delayDuration={200}>
              <ListPage<AppSecret>
                embedded
                list={localizeSecretList(keysList, t)}
                label={t("title")}
                columns={columns}
                rows={keyRows}
                getRowId={(s) => s.id}
                loading={secretsLoading}
                error={secretsError && list.length === 0 ? secretsError : null}
                onRetry={retrySecrets}
                totalCount={keyTotal}
                empty={{
                  icon: <KeyIcon className="size-5" />,
                  title: t("emptyTitle"),
                  description: t("emptyDescription"),
                }}
              />
            </TooltipProvider>
          </>
        )}
      </DetailTabSections>

      <Sheet
        open={historyTarget !== null}
        onOpenChange={(next) => {
          if (!next) setHistoryTarget(null);
        }}
      >
        <SheetContent className="flex flex-col sm:max-w-md">
          <SheetHeader>
            <SheetTitle>{t("history.title")}</SheetTitle>
            <SheetDescription className="font-mono [overflow-wrap:anywhere]">
              {historyTarget?.key}
            </SheetDescription>
          </SheetHeader>
          <div className="min-h-0 flex-1 overflow-y-auto">
            {historyTarget && renderHistory(historyTarget.key)}
          </div>
        </SheetContent>
      </Sheet>

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

type SecretsT = ReturnType<typeof useTranslations<"apps.secrets">>;

function localizeSecretList(
  list: ListStateController,
  t: SecretsT,
  bundles = false
): ListStateController {
  const options: Record<string, string> = {
    literal: t("source.literal"),
    bundle: t("source.bundle"),
    managed_service: t("source.managed"),
    all: t("scope.all"),
    production: t("scope.production"),
    preview: t("scope.preview"),
  };
  return {
    ...list,
    definition: {
      ...list.definition,
      searchPlaceholder: t(bundles ? "list.searchBundles" : "list.searchKeys"),
      fields: list.definition.fields.map((field) => ({
        ...field,
        label:
          field.key === "source"
            ? t("columns.source")
            : field.key === "scope"
              ? t("scope.title")
              : field.label,
        options: field.options?.map((option) => ({
          ...option,
          label: options[option.value] ?? option.label,
        })),
      })),
      views: list.definition.views.map((view) => ({
        ...view,
        label: view.key === "all" ? t("list.all") : view.label,
        note: t("list.localNote"),
      })),
    },
  };
}

interface SecretColumnsArgs {
  t: SecretsT;
  formatDateTime: (date: string) => string;
  revealedValues: Record<string, string>;
  editingId: string | null;
  rotatingId: string | null;
  revealingId: string | null;
  historyId: string | null;
  busy: boolean;
  onToggleReveal: (s: AppSecret) => void;
  onStartEdit: (s: AppSecret) => void;
  onStartRotate: (s: AppSecret) => void;
  onCancelEdit: () => void;
  onInlineSave: (s: AppSecret, value: string) => Promise<boolean>;
  onRequestDelete: (s: AppSecret) => void;
  onOpenHistory: (s: AppSecret) => void;
}

/** The secrets table's columns; each cell reads the row plus the screen's edit state. */
function secretColumns({
  t,
  formatDateTime,
  revealedValues,
  editingId,
  rotatingId,
  revealingId,
  historyId,
  busy,
  onToggleReveal,
  onStartEdit,
  onStartRotate,
  onCancelEdit,
  onInlineSave,
  onRequestDelete,
  onOpenHistory,
}: SecretColumnsArgs): Column<AppSecret>[] {
  const sourceLabels: Record<string, string> = {
    literal: t("source.literal"),
    bundle: t("source.bundle"),
    managed_service: t("source.managed"),
  };
  const setViaLabels: Record<string, string> = {
    web: t("setVia.web"),
    cli: t("setVia.cli"),
    env_paste: t("setVia.envPaste"),
    bundle: t("setVia.bundle"),
    managed_service: t("setVia.managed"),
  };
  const revealedOf = (s: AppSecret) => (s.id in revealedValues ? revealedValues[s.id] : null);
  return [
    {
      id: "key",
      header: t("columns.key"),
      sortKey: "key",
      cellClassName: "whitespace-normal",
      cell: (s) => (
        <span className="flex min-w-0 flex-wrap items-center">
          <code className="min-w-0 font-mono text-xs [overflow-wrap:anywhere]">{s.key}</code>
          <SecretScopeBadge scope={s.scope} />
          <SecretExpiryBadge expiresAt={s.expiresAt} />
          <SecretPendingApprovalBadge source={s.source} deploysAsShown={s.deploysAsShown} />
        </span>
      ),
    },
    {
      id: "value",
      header: t("columns.value"),
      cellClassName: "font-mono text-xs whitespace-normal",
      cell: (s) => {
        const revealed = revealedOf(s);
        const isRevealed = revealed !== null;
        const canEdit = s.source === "literal" && isRevealed;
        const editing = editingId === s.id || rotatingId === s.id;
        return editing && canEdit ? (
          <InlineValueEditor
            initial={revealed ?? ""}
            busy={busy}
            onSave={(value) => onInlineSave(s, value)}
            onCancel={onCancelEdit}
          />
        ) : isRevealed ? (
          <ValueRevealed
            value={revealed}
            canEdit={s.source === "literal"}
            onStartEdit={() => onStartEdit(s)}
          />
        ) : (
          <span className="text-muted-foreground select-none" aria-label={t("reveal.masked")}>
            ••••••••
          </span>
        );
      },
    },
    {
      id: "source",
      header: t("columns.source"),
      cellClassName: "whitespace-normal",
      cell: (s) => (
        <span className="flex min-w-0 flex-wrap items-center gap-2">
          <Badge variant={SOURCE_TONE[s.source] ?? "outline"}>
            {sourceLabels[s.source] ?? s.source}
          </Badge>
          {s.bundleSlug && (
            <span className="text-muted-foreground text-2xs min-w-0 font-mono [overflow-wrap:anywhere]">
              {s.bundleSlug}
            </span>
          )}
          {s.managedServiceKind && (
            <span className="text-muted-foreground text-2xs font-mono">{s.managedServiceKind}</span>
          )}
        </span>
      ),
    },
    {
      id: "env",
      sortKey: "env",
      header: t("columns.env"),
      cell: (s) => (
        <Badge variant="outline" className="text-2xs max-w-48 truncate font-mono">
          {s.environmentName || "—"}
        </Badge>
      ),
    },
    {
      id: "edited",
      sortKey: "edited",
      header: t("columns.lastEdited"),
      cellClassName: "text-muted-foreground font-mono text-xs",
      cell: (s) => {
        const editorName = s.lastEditedBy?.displayName || s.lastEditedBy?.username;
        const setViaLabel = setViaLabels[s.setVia ?? "web"] ?? s.setVia ?? "web";
        // "edited by X · set via Y"; the "by X" half drops when the writer
        // is unknown (backfilled rows).
        const tip = editorName
          ? `${t("lastEditedBy", { name: editorName })} · ${t("setVia.description", { source: setViaLabel })}`
          : `${t("lastEditedByUnknown")} · ${t("setVia.description", { source: setViaLabel })}`;
        return (
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="cursor-help">
                {s.lastEditedAt ? formatDateTime(s.lastEditedAt) : "—"}
              </span>
            </TooltipTrigger>
            <TooltipContent>{tip}</TooltipContent>
          </Tooltip>
        );
      },
    },
    {
      id: "actions",
      header: <span className="sr-only">{t("columns.actions")}</span>,
      align: "right",
      width: "w-40",
      cell: (s) => {
        const revealed = revealedOf(s);
        const isRevealed = revealed !== null;
        const canEdit = s.source === "literal" && isRevealed;
        const editing = editingId === s.id || rotatingId === s.id;
        if (s.source !== "literal") return null;
        return (
          <div className="inline-flex items-center gap-1">
            <Can permission="secret.read">
              <IconAction
                label={isRevealed ? t("reveal.hide") : t("reveal.show")}
                onClick={() => onToggleReveal(s)}
                disabled={busy || revealingId === s.id}
                pressed={isRevealed}
              >
                {revealingId === s.id ? (
                  <Loader2Icon className="size-4 animate-spin" />
                ) : isRevealed ? (
                  <EyeOffIcon className="size-4" />
                ) : (
                  <EyeIcon className="size-4" />
                )}
              </IconAction>
            </Can>
            {/* #714: Rotate edits like Edit but saves through rotateAppSecret,
                so the audit log says app.secret.rotate. Revealed literals only,
                as Edit. */}
            {canEdit && (
              <Can permission="app.deploy">
                <IconAction
                  label={t("rotate")}
                  tooltip={t("rotateHint")}
                  onClick={() => onStartRotate(s)}
                  disabled={busy || editing}
                  pressed={rotatingId === s.id}
                >
                  <RotateCwIcon className="size-4" />
                </IconAction>
              </Can>
            )}
            {/* #714: the key's audit timeline, in a side sheet; its query runs only while open. */}
            <Can permission="secret.read">
              <IconAction
                label={t("history.action")}
                tooltip={t("history.actionHint")}
                onClick={() => onOpenHistory(s)}
                pressed={historyId === s.id}
              >
                <HistoryIcon className="size-4" />
              </IconAction>
            </Can>
            <Can permission="app.deploy">
              <IconAction
                label={t("delete.confirm")}
                onClick={() => onRequestDelete(s)}
                disabled={busy}
              >
                <Trash2Icon className="size-4" />
              </IconAction>
            </Can>
          </div>
        );
      },
    },
  ];
}

function IconAction({
  label,
  tooltip,
  onClick,
  disabled,
  pressed,
  children,
}: {
  label: string;
  tooltip?: string;
  onClick: () => void;
  disabled?: boolean;
  pressed?: boolean;
  children: React.ReactNode;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="size-8"
          onClick={onClick}
          disabled={disabled}
          aria-pressed={pressed}
          aria-label={label}
        >
          {children}
        </Button>
      </TooltipTrigger>
      <TooltipContent>{tooltip ?? label}</TooltipContent>
    </Tooltip>
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

function AttachedBundlesList({
  list,
  rows,
  totalCount,
  loading,
  error,
  onRetry,
  onDetach,
  busy,
}: {
  list: ListStateController;
  rows: AppSecretBundleAttachment[];
  totalCount: number;
  loading: boolean;
  error: { message: string } | null;
  onRetry: () => void;
  onDetach: (a: AppSecretBundleAttachment) => void;
  busy: boolean;
}) {
  const t = useTranslations("apps.secrets.attached");
  const all = useTranslations("apps.secrets");
  const fmt = useFormatters();
  const columns: Column<AppSecretBundleAttachment>[] = [
    {
      id: "bundle",
      header: t("columns.bundle"),
      sortKey: "bundle",
      cellClassName: "whitespace-normal",
      cell: (a) => (
        <span className="flex min-w-0 flex-wrap items-baseline gap-2 font-mono text-xs">
          <span className="min-w-0 [overflow-wrap:anywhere]">{a.bundleName}</span>
          <span className="text-muted-foreground text-2xs min-w-0 [overflow-wrap:anywhere]">
            {a.bundleSlug}
          </span>
        </span>
      ),
    },
    {
      id: "team",
      header: t("columns.team"),
      cell: (a) =>
        a.teamSlug ? (
          <Badge variant="outline" className="text-2xs max-w-48 truncate font-mono">
            {a.teamSlug}
          </Badge>
        ) : (
          <span className="text-muted-foreground text-xs">{t("noTeam")}</span>
        ),
    },
    {
      id: "env",
      header: t("columns.env"),
      cell: (a) => (
        <Badge variant="outline" className="text-2xs max-w-48 truncate font-mono">
          {t("perEnvBadge", { env: a.environmentName })}
        </Badge>
      ),
    },
    {
      id: "prefix",
      header: t("columns.prefix"),
      cellClassName: "font-mono text-xs",
      cell: (a) => a.prefix || <span className="text-muted-foreground">{t("noPrefix")}</span>,
    },
    {
      id: "keys",
      header: t("columns.keys"),
      cellClassName: "font-mono text-xs tabular-nums",
      cell: (a) => (a.keyCount > 0 ? a.keyCount : t("keyCountUnknown")),
    },
    {
      id: "order",
      header: t("columns.order"),
      sortKey: "order",
      cell: (a) => (
        <Tooltip>
          <TooltipTrigger asChild>
            <Badge variant="secondary" className="font-mono">
              {a.mergeOrder}
            </Badge>
          </TooltipTrigger>
          <TooltipContent>{t("orderHint")}</TooltipContent>
        </Tooltip>
      ),
    },
    {
      id: "attached",
      header: t("columns.attached"),
      sortKey: "attached",
      cellClassName: "text-muted-foreground font-mono text-xs",
      cell: (a) => (a.attachedAt ? fmt.formatDateTime(a.attachedAt) : "—"),
    },
    {
      id: "actions",
      header: <span className="sr-only">{t("detach")}</span>,
      align: "right",
      cell: (a) => (
        <Can permission="app.deploy">
          <Button variant="ghost" size="sm" onClick={() => onDetach(a)} disabled={busy}>
            {t("detach")}
          </Button>
        </Can>
      ),
    },
  ];

  return (
    <div className="min-w-0 space-y-3">
      <p className="text-muted-foreground text-sm">{t("description")}</p>
      <TooltipProvider delayDuration={200}>
        <ListPage<AppSecretBundleAttachment>
          embedded
          list={localizeSecretList(list, all, true)}
          label={t("title")}
          columns={columns}
          rows={rows}
          getRowId={(a) => a.id}
          loading={loading}
          error={error}
          onRetry={onRetry}
          totalCount={totalCount}
          empty={{ icon: <LayersIcon className="size-5" />, title: t("empty") }}
        />
      </TooltipProvider>
    </div>
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
            <Label htmlFor="secret-scope">{t("scope")}</Label>
            <Select value={scope} onValueChange={setScope}>
              <SelectTrigger id="secret-scope">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">{t("allScope")}</SelectItem>
                <SelectItem value="production">{t("productionScope")}</SelectItem>
                <SelectItem value="preview">{t("previewScope")}</SelectItem>
                <SelectItem value={SCOPE_PREVIEW_BRANCH}>{t("branchScope")}</SelectItem>
              </SelectContent>
            </Select>
            {needsBranchInput && (
              <Input
                id="secret-scope-branch"
                aria-label={t("branchLabel")}
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
                ? t("allHint")
                : scope === "production"
                  ? t("productionHint")
                  : scope === "preview"
                    ? t("previewHint")
                    : t("branchHint")}
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
