"use client";

import {
  AlertTriangleIcon,
  CheckIcon,
  CodeIcon,
  ExternalLinkIcon,
  GitPullRequestIcon,
  LayoutIcon,
  RefreshCwIcon,
  SaveIcon,
} from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import type { ManifestSyncState } from "@/graphql/registry/registry.types";
import { useFormatters } from "@/lib/i18n/formatters";

import { ManifestFormPane } from "./ManifestFormPane";
import type { useConfigEditor } from "./use-config-editor";

/** What a Form-view builder pane receives; the agent-config pane is a slot. */
export interface ConfigFormPaneProps {
  draft: string;
  onDraftChange: (toml: string) => void;
  onSwitchToCode: () => void;
}

export type ConfigEditorScreenProps = ReturnType<typeof useConfigEditor> & {
  slug: string;
  /** The app tab bar. */
  tabs?: React.ReactNode;
  /** Form view for an agent config repo (#1172). */
  renderAgentConfigForm?: (props: ConfigFormPaneProps) => React.ReactNode;
};

const SYNC_BADGE: Record<
  ManifestSyncState,
  { key: string; tone: "default" | "secondary" | "destructive" | "outline" }
> = {
  in_sync: { key: "inSync", tone: "secondary" },
  db_ahead: { key: "dbAhead", tone: "outline" },
  repo_ahead: { key: "repoAhead", tone: "outline" },
  diverged: { key: "diverged", tone: "destructive" },
};

// Section keys map to the leading [section] header in astrolift.toml.
// The pill toggle scrolls + selects the first occurrence inside the
// textarea, including array-of-tables headers written by the manifest codec.
const PILL_SECTIONS: Array<{ key: string; headers: string[] }> = [
  { key: "workloads", headers: ["[workloads", "[[workloads"] },
  { key: "services", headers: ["[managed_services", "[[managed_services"] },
  { key: "env", headers: ["[env", "[[env"] },
  { key: "volumes", headers: ["[volumes", "[[volumes"] },
];

function PillToggleGroup({
  onSelect,
  active,
}: {
  onSelect: (key: string) => void;
  active: string | null;
}) {
  const t = useTranslations("apps.config.pills");
  return (
    <div className="flex flex-wrap gap-1.5">
      {PILL_SECTIONS.map((p) => {
        const isActive = p.key === active;
        return (
          <button
            key={p.key}
            type="button"
            onClick={() => onSelect(p.key)}
            className={`rounded-full border px-3 py-1 text-xs transition-colors ${
              isActive
                ? "border-foreground bg-foreground text-background"
                : "border-input hover:bg-accent hover:text-accent-foreground"
            }`}
          >
            {t(p.key)}
          </button>
        );
      })}
    </div>
  );
}

function ConflictResolverModal({
  ours,
  theirs,
  serverUpdatedAt,
  onKeepMine,
  onAcceptTheirs,
  onClose,
}: {
  ours: string;
  theirs: string;
  serverUpdatedAt: string;
  onKeepMine: () => void;
  onAcceptTheirs: () => void;
  onClose: () => void;
}) {
  const t = useTranslations("apps.config.conflict");
  const fmt = useFormatters();
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-6"
      role="dialog"
      aria-modal="true"
      aria-labelledby="conflict-title"
    >
      <div className="bg-background w-full max-w-4xl rounded-lg border p-6 shadow-2xl">
        <h2 id="conflict-title" className="text-lg font-semibold">
          {t("title")}
        </h2>
        <p className="text-muted-foreground mt-1 text-sm">
          {t("description", { at: fmt.formatDateTime(serverUpdatedAt) })}
        </p>
        <div className="mt-4 grid gap-4 md:grid-cols-2">
          <div>
            <div className="text-muted-foreground mb-1 text-xs tracking-wider uppercase">
              {t("yourDraft")}
            </div>
            <pre className="bg-muted text-2xs max-h-[50vh] overflow-auto rounded p-3 font-mono leading-relaxed">
              {ours}
            </pre>
          </div>
          <div>
            <div className="text-muted-foreground mb-1 text-xs tracking-wider uppercase">
              {t("serverCopy")}
            </div>
            <pre className="bg-muted text-2xs max-h-[50vh] overflow-auto rounded p-3 font-mono leading-relaxed">
              {theirs}
            </pre>
          </div>
        </div>
        <div className="mt-6 flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>
            {t("cancel")}
          </Button>
          <Button variant="outline" onClick={onAcceptTheirs}>
            {t("loadTheirs")}
          </Button>
          <Button onClick={onKeepMine}>{t("keepMine")}</Button>
        </div>
      </div>
    </div>
  );
}

/**
 * App config tab: the manifest as a visual builder (Form) or raw TOML with the
 * rendered-resources preview (Code), plus save / sync / push / apply and the
 * conflict resolver. Pure view over useConfigEditor.
 */
export function ConfigEditorScreen({
  slug,
  tabs,
  renderAgentConfigForm,
  app: a,
  loading,
  draft,
  setDraft,
  schemaFamily,
  isDirty,
  rendered: renderedResult,
  renderedLoading,
  busy,
  saving,
  applying,
  changedEnvKeys,
  conflict,
  handleSave,
  handleSync,
  handleApply,
  handlePush,
  dismissConflictKeepMine,
  adoptTheirs,
  closeConflict,
}: ConfigEditorScreenProps) {
  const fmt = useFormatters();
  const tCommon = useTranslations("apps.common");
  const t = useTranslations("apps.config");
  const tSync = useTranslations("apps.config.syncStates");

  const [activePill, setActivePill] = React.useState<string | null>(null);
  // Form (visual builder) vs Code (raw TOML editor). The visual builder is the
  // default surface; the raw editor stays a strict superset (#1110).
  const [view, setView] = React.useState<"form" | "code">("form");
  const textareaRef = React.useRef<HTMLTextAreaElement>(null);
  const [confirmSync, setConfirmSync] = React.useState(false);
  const [confirmApply, setConfirmApply] = React.useState(false);

  function scrollToSection(key: string) {
    const section = PILL_SECTIONS.find((p) => p.key === key);
    if (!section || !textareaRef.current) return;
    const ta = textareaRef.current;
    const text = ta.value;
    // Find the first occurrence of any header prefix on a line. We
    // walk line-by-line so a "[workloads" inside a comment or value
    // doesn't false-match.
    const lines = text.split("\n");
    let charOffset = 0;
    let foundLine = -1;
    for (let i = 0; i < lines.length; i++) {
      const trimmed = lines[i].trimStart();
      if (section.headers.some((h) => trimmed.startsWith(h))) {
        foundLine = i;
        break;
      }
      charOffset += lines[i].length + 1;
    }
    if (foundLine === -1) {
      const tPills = (k: string) =>
        ({
          workloads: "workloads",
          services: "managed services",
          env: "env vars",
          volumes: "volumes",
        })[k] ?? k;
      toast.message(t("pills.noSection", { section: tPills(section.key) }));
      return;
    }
    setActivePill(key);
    ta.focus();
    // Set selection to the header line so the textarea scrolls to it.
    const headerEnd = charOffset + lines[foundLine].length;
    ta.setSelectionRange(charOffset, headerEnd);
    // Manual scroll — chromium ignores setSelectionRange for scroll in
    // some configurations. Approximate by setting scrollTop based on
    // line height.
    const lineHeight = parseFloat(window.getComputedStyle(ta).lineHeight || "16");
    ta.scrollTop = Math.max(0, foundLine * lineHeight - 40);
  }

  if (loading) {
    return (
      <PageShell title={t("loadingTitle")} description={t("loading")}>
        <Skeleton className="h-96 w-full" />
      </PageShell>
    );
  }

  if (!a) {
    return (
      <PageShell title={tCommon("notFound")} description={tCommon("notFoundPermission")}>
        <EmptyState
          icon={<AlertTriangleIcon className="size-5" />}
          title={tCommon("notFoundSlug", { slug })}
          actionHref="/apps"
          actionLabel={tCommon("backToApps")}
        />
      </PageShell>
    );
  }

  const syncBadge = SYNC_BADGE[a.manifestSyncState] ?? SYNC_BADGE.in_sync;
  const formPaneProps: ConfigFormPaneProps = {
    draft,
    onDraftChange: setDraft,
    onSwitchToCode: () => setView("code"),
  };

  return (
    <PageShell
      title={t("title", { name: a.name })}
      description={
        <span className="text-muted-foreground flex flex-wrap items-center gap-2 font-mono text-xs">
          <span>{t("repoLocation", { path: a.manifestPath, branch: a.deployBranch })}</span>
          <Badge variant={syncBadge.tone}>{tSync(syncBadge.key)}</Badge>
          <span>{t("updated", { at: fmt.formatRelativeTime(a.updatedAt) })}</span>
        </span>
      }
      actions={
        <>
          <div className="flex items-center gap-1 rounded-md border p-0.5">
            <Button
              variant={view === "form" ? "secondary" : "ghost"}
              size="sm"
              className="h-7 px-2.5"
              onClick={() => setView("form")}
            >
              <LayoutIcon className="size-3.5" />
              {t("view.form")}
            </Button>
            <Button
              variant={view === "code" ? "secondary" : "ghost"}
              size="sm"
              className="h-7 px-2.5"
              onClick={() => setView("code")}
            >
              <CodeIcon className="size-3.5" />
              {t("view.code")}
            </Button>
          </div>
          <Button variant="outline" onClick={() => setConfirmSync(true)} disabled={busy}>
            <RefreshCwIcon className="size-4" />
            {t("syncFromRepo")}
          </Button>
          <Button onClick={handleSave} disabled={busy || !isDirty}>
            <SaveIcon className="size-4" />
            {saving ? t("saving") : t("saveDraft")}
          </Button>
          {a.sourceRepo ? (
            <Button
              variant="outline"
              onClick={handlePush}
              disabled={busy}
              title={a.manifestSyncState === "in_sync" ? t("pushHint") : undefined}
            >
              <GitPullRequestIcon className="size-4" />
              {t("pushToRepo")}
            </Button>
          ) : (
            // No source connection to push a PR through (#1759), so apply
            // the staged draft directly rather than leaving it frozen in
            // manifest_raw_staged forever.
            <Button
              variant="outline"
              onClick={() => setConfirmApply(true)}
              disabled={busy || !a.rawManifestStaged || isDirty}
              title={isDirty ? t("applyHint") : undefined}
            >
              <CheckIcon className="size-4" />
              {applying ? t("applying") : t("applyStaged")}
            </Button>
          )}
        </>
      }
    >
      {tabs}
      {conflict && (
        <Card className="border-destructive/40 bg-destructive/5">
          <CardContent className="flex flex-wrap items-center justify-between gap-3 p-4 text-sm">
            <div className="flex items-center gap-2">
              <AlertTriangleIcon className="text-destructive size-4" />
              <span>
                {t("conflict.banner", { at: fmt.formatRelativeTime(conflict.serverUpdatedAt) })}
              </span>
            </div>
            <div className="flex gap-2">
              <Button size="sm" variant="outline" onClick={dismissConflictKeepMine}>
                {t("conflict.keepMine")}
              </Button>
              <Button size="sm" variant="outline" onClick={adoptTheirs}>
                {t("conflict.loadTheirsShort")}
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {view === "form" ? (
        schemaFamily === "agent_config" ? (
          renderAgentConfigForm?.(formPaneProps)
        ) : (
          <ManifestFormPane {...formPaneProps} />
        )
      ) : (
        <>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <PillToggleGroup onSelect={scrollToSection} active={activePill} />
            <p className="text-muted-foreground text-xs">{t("pills.jumpHint")}</p>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle className="text-base">
                  <span className="font-mono">{a.manifestPath}</span>
                  {isDirty && (
                    <Badge variant="outline" className="text-2xs ml-2">
                      {t("editor.unsaved")}
                    </Badge>
                  )}
                  {!a.sourceRepo && a.rawManifestStaged && (
                    <Badge variant="outline" className="text-2xs ml-2">
                      {t("editor.stagedNotApplied")}
                    </Badge>
                  )}
                </CardTitle>
                <CardDescription>{t("editor.description")}</CardDescription>
              </CardHeader>
              <CardContent>
                <Textarea
                  ref={textareaRef}
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  rows={26}
                  spellCheck={false}
                  className="font-mono text-xs"
                  placeholder="# astrolift.toml"
                />
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="text-base">{t("rendered.title")}</CardTitle>
                <CardDescription>{t("rendered.description")}</CardDescription>
              </CardHeader>
              <CardContent className="p-0">
                {renderedLoading ? (
                  <Skeleton className="m-6 h-96" />
                ) : renderedResult?.error ? (
                  <div className="text-destructive p-6 text-sm">
                    <p className="font-medium">{t("rendered.renderFailed")}</p>
                    <p className="mt-1">{renderedResult.error}</p>
                    {renderedResult.errorPath && (
                      <p className="text-muted-foreground mt-2 font-mono text-xs">
                        {renderedResult.errorPath}
                        {renderedResult.errorLine != null && `:${renderedResult.errorLine}`}
                        {renderedResult.errorColumn != null && `:${renderedResult.errorColumn}`}
                      </p>
                    )}
                  </div>
                ) : renderedResult ? (
                  <pre className="bg-muted m-4 max-h-[640px] overflow-auto rounded p-3 font-mono text-xs leading-relaxed">
                    {JSON.stringify(renderedResult.resources, null, 2)}
                  </pre>
                ) : (
                  <div className="text-muted-foreground p-6 text-sm">{t("rendered.noOutput")}</div>
                )}
              </CardContent>
            </Card>
          </div>
        </>
      )}

      {a.sourceUrl && (
        <Card className="border-dashed">
          <CardContent className="flex items-center justify-between gap-3 p-4 text-sm">
            <span className="text-muted-foreground">
              {t("sourceRepo")} <span className="font-mono">{a.sourceRepo}</span> {t("onBranch")}{" "}
              <span className="font-mono">{a.deployBranch}</span>
            </span>
            <Button asChild variant="ghost" size="sm">
              <a href={a.sourceUrl} target="_blank" rel="noreferrer">
                <ExternalLinkIcon className="size-3.5" />
                {t("openRepo")}
              </a>
            </Button>
          </CardContent>
        </Card>
      )}

      {conflict && (
        <ConflictResolverModal
          ours={draft}
          theirs={conflict.theirs}
          serverUpdatedAt={conflict.serverUpdatedAt}
          onKeepMine={dismissConflictKeepMine}
          onAcceptTheirs={adoptTheirs}
          onClose={closeConflict}
        />
      )}

      <ConfirmDialog
        open={confirmSync}
        onOpenChange={setConfirmSync}
        title={t("confirmSync.title")}
        description={t("confirmSync.description")}
        confirmLabel={t("confirmSync.confirm")}
        destructive
        onConfirm={handleSync}
      />

      <ConfirmDialog
        open={confirmApply}
        onOpenChange={setConfirmApply}
        title={t("confirmApply.title")}
        description={
          changedEnvKeys.length > 0
            ? t("confirmApply.descriptionWithEnv", { keys: changedEnvKeys.join(", ") })
            : t("confirmApply.description")
        }
        confirmLabel={t("confirmApply.confirm")}
        onConfirm={handleApply}
      />
    </PageShell>
  );
}
