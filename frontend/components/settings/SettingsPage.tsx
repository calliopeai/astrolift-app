"use client";

/**
 * SettingsPage — the settings archetype (spec 44 §5.3).
 *
 *   ┌──────────────┬───────────────────────────────────────────┐
 *   │ Central auth │ Central auth                               │
 *   │ Ingress      │ What this does, one sentence.              │
 *   │ Danger zone  │ Field ________  help          [Cancel][Save]│
 *   └──────────────┴───────────────────────────────────────────┘
 *
 * One `SettingsSection` per concern, each saving on its own: there is no
 * page-wide save. The section nav collapses to a select below `md`.
 * Destructive settings go in the one `dangerZone`, each a `DangerAction`
 * behind a ConfirmDialog. Someone without the permission sees the same
 * fields, disabled, and a sentence naming the permission that would allow it.
 *
 *   <SettingsPage
 *     sections={[{ id: "auth", title: "Central auth", content: <AuthSection … /> }]}
 *     dangerZone={<DangerAction title="Delete cluster" … onConfirm={deleteCluster} />}
 *     readOnly={canManage ? undefined : { permission: "cluster.manage" }}
 *   />
 *
 * where each section's content is a `SettingsSection` with its own form
 * state and `onSave`. Pure: permission checks and mutations are the hook's.
 *
 * Single-section mode, for tabs whose sections are heavy (a config editor,
 * a shell console): only the active section is mounted, chosen by
 * `?section=` in the URL. The nav still lists every section (a select below
 * `md`); an absent or unknown id shows the first. Without `single` every
 * section is mounted and the nav scrolls to it, as before.
 *
 *   const section = useSettingsSection();       // use-settings-section.ts
 *   <SettingsPage single={section} sections={[…]} dangerZone={…} />
 */

import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import { Section } from "@/components/ui/section";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { type RestrictedSettings } from "@/lib/display-prefs";
import { cn } from "@/lib/utils";

import { useRestrictedMode } from "./Restricted";
import type { SectionSelection } from "./use-settings-section";

const DANGER_ID = "danger-zone";

/** The page is read-only because the viewer lacks this permission. */
export interface ReadOnlyReason {
  /** The permission key, e.g. `cluster.manage`. */
  permission: string;
}

const ReadOnlyContext = React.createContext<ReadOnlyReason | undefined>(undefined);

export interface SettingsSectionSpec {
  /** The anchor: `#<id>`. */
  id: string;
  title: string;
  content: React.ReactNode;
}

export interface SettingsPageProps {
  sections: SettingsSectionSpec[];
  /** `DangerAction`s; the section is left out when absent. */
  dangerZone?: React.ReactNode;
  readOnly?: ReadOnlyReason;
  /** Overrides the person's "settings you can't change" preference (stories). */
  restrictedMode?: RestrictedSettings;
  /** Mount only the active section, chosen by `?section=` (useSettingsSection). */
  single?: SectionSelection;
  className?: string;
}

export function SettingsPage({
  sections: allSections,
  dangerZone: danger,
  readOnly,
  restrictedMode,
  single,
  className,
}: SettingsPageProps) {
  const t = useTranslations("shared.settings");
  // A person who hides what they can't change sees only the notice on a
  // page that is read-only as a whole.
  const hideAll = useRestrictedMode(restrictedMode) === "hide" && Boolean(readOnly);
  const sections = hideAll ? [] : allSections;
  const dangerZone = hideAll ? undefined : danger;
  const nav = [
    ...sections.map((s) => ({ id: s.id, title: s.title })),
    ...(dangerZone ? [{ id: DANGER_ID, title: t("dangerZone") }] : []),
  ];
  const [scrolledTo, setScrolledTo] = React.useState(nav[0]?.id);
  const active = single ? (nav.find((n) => n.id === single.active) ?? nav[0])?.id : scrolledTo;
  const shown = (id: string) => !single || id === active;

  const go = (id: string) => {
    if (single) {
      single.select(id);
      return;
    }
    setScrolledTo(id);
    document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  return (
    <ReadOnlyContext.Provider value={readOnly}>
      <div className={cn("flex min-w-0 flex-col gap-6 md:flex-row md:items-start", className)}>
        <nav aria-label={t("sections")} className="min-w-0 md:sticky md:top-6 md:w-48 md:shrink-0">
          <div className="md:hidden">
            <Select value={active} onValueChange={go}>
              <SelectTrigger aria-label={t("section")} className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {nav.map((n) => (
                  <SelectItem key={n.id} value={n.id}>
                    {n.title}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <ul className="hidden flex-col gap-0.5 md:flex">
            {nav.map((n) => (
              <li key={n.id}>
                {single ? (
                  <Link
                    href={single.href(n.id)}
                    scroll={false}
                    onClick={(e) => {
                      // A plain click selects in place; a modified one opens the link.
                      if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0)
                        return;
                      e.preventDefault();
                      single.select(n.id);
                    }}
                    aria-current={active === n.id ? "page" : undefined}
                    className={navItemClass(active === n.id, n.id)}
                  >
                    {n.title}
                  </Link>
                ) : (
                  <a
                    href={`#${n.id}`}
                    onClick={() => setScrolledTo(n.id)}
                    aria-current={active === n.id ? "location" : undefined}
                    className={navItemClass(active === n.id, n.id)}
                  >
                    {n.title}
                  </a>
                )}
              </li>
            ))}
          </ul>
        </nav>

        <div className="flex min-w-0 flex-1 flex-col gap-10">
          {readOnly && <ReadOnlyNotice permission={readOnly.permission} />}
          {sections
            .filter((s) => shown(s.id))
            .map((s) => (
              <div key={s.id} id={s.id} className="min-w-0 scroll-mt-6">
                {s.content}
              </div>
            ))}
          {dangerZone && shown(DANGER_ID) && (
            <div id={DANGER_ID} className="min-w-0 scroll-mt-6">
              <Section
                title={t("dangerZone")}
                description={t("dangerDescription")}
                className="border-danger-border rounded-md border p-4"
              >
                <fieldset disabled={Boolean(readOnly)} className="flex min-w-0 flex-col divide-y">
                  {dangerZone}
                </fieldset>
              </Section>
            </div>
          )}
        </div>
      </div>
    </ReadOnlyContext.Provider>
  );
}

function navItemClass(isActive: boolean, id: string) {
  return cn(
    "block min-w-0 truncate rounded-sm px-2 py-1.5 text-sm transition-colors",
    isActive
      ? "bg-muted text-foreground font-medium"
      : "text-muted-foreground hover:text-foreground",
    id === DANGER_ID && "text-danger hover:text-danger"
  );
}

function ReadOnlyNotice({ permission }: { permission: string }) {
  const t = useTranslations("shared.settings");
  return (
    <p className="bg-muted text-muted-foreground rounded-md border px-3 py-2 text-sm">
      {t.rich("readOnly", {
        permission,
        code: (chunks) => (
          <code className="text-foreground font-mono [overflow-wrap:anywhere]">{chunks}</code>
        ),
      })}
    </p>
  );
}

// ---------------------------------------------------------------------------

export interface SettingsSectionProps {
  title: string;
  /** What this does, one sentence. */
  description?: React.ReactNode;
  children: React.ReactNode;
  /** Saves this section alone. A rejected promise keeps the edits. */
  onSave?: () => Promise<unknown> | unknown;
  /** Put the fields back to what is saved. */
  onCancel?: () => void;
  /** Unsaved edits: enables Save and Cancel. */
  dirty?: boolean;
  saving?: boolean;
  /** An error from the last save, shown beside the buttons. */
  error?: string | null;
  saveLabel?: string;
}

/**
 * One concern, one form, one Save. Inside a read-only SettingsPage the
 * fields are disabled (a disabled fieldset) and the buttons are gone.
 */
export function SettingsSection({
  title,
  description,
  children,
  onSave,
  onCancel,
  dirty = false,
  saving = false,
  error,
  saveLabel,
}: SettingsSectionProps) {
  const t = useTranslations("shared.settings");
  const readOnly = React.useContext(ReadOnlyContext);
  return (
    <Section title={title} description={description} divided>
      <form
        className="flex min-w-0 flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (!readOnly && dirty && !saving) void onSave?.();
        }}
      >
        <fieldset disabled={Boolean(readOnly) || saving} className="flex min-w-0 flex-col gap-4">
          {children}
        </fieldset>
        {!readOnly && onSave && (
          <div className="flex min-w-0 flex-wrap items-center justify-end gap-2">
            {error && (
              <p
                role="alert"
                className="text-danger mr-auto min-w-0 text-sm [overflow-wrap:anywhere]"
              >
                {error}
              </p>
            )}
            {onCancel && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={onCancel}
                disabled={!dirty || saving}
              >
                {t("cancel")}
              </Button>
            )}
            <Button type="submit" size="sm" disabled={!dirty || saving}>
              {saving ? t("saving") : (saveLabel ?? t("save"))}
            </Button>
          </div>
        )}
      </form>
    </Section>
  );
}

// ---------------------------------------------------------------------------

export interface DangerActionProps {
  title: string;
  description: React.ReactNode;
  /** The button, e.g. "Delete cluster". */
  actionLabel: string;
  confirmTitle: React.ReactNode;
  /** The blast radius: what goes, what is recoverable, who can undo. */
  confirmDescription?: React.ReactNode;
  onConfirm: (reason: string) => Promise<unknown> | unknown;
  /** Ask for a reason the audit trail keeps. */
  reasonLabel?: string;
  /** Why this action is not offered here: the button is disabled and this is shown. */
  disabledReason?: string | null;
}

/** A row in the Danger zone: what it does, and a button behind a ConfirmDialog. */
export function DangerAction({
  title,
  description,
  actionLabel,
  confirmTitle,
  confirmDescription,
  onConfirm,
  reasonLabel,
  disabledReason,
}: DangerActionProps) {
  const [open, setOpen] = React.useState(false);
  const reasonId = React.useId();
  return (
    <div className="flex min-w-0 flex-col gap-3 py-3 first:pt-0 last:pb-0 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0">
        <p className="text-sm font-medium">{title}</p>
        <p className="text-muted-foreground text-sm [overflow-wrap:anywhere]">{description}</p>
        {disabledReason && (
          <p id={reasonId} className="text-warning-fg mt-1 text-sm [overflow-wrap:anywhere]">
            {disabledReason}
          </p>
        )}
      </div>
      <Button
        type="button"
        variant="destructive"
        size="sm"
        className="shrink-0"
        disabled={!!disabledReason}
        aria-describedby={disabledReason ? reasonId : undefined}
        onClick={() => setOpen(true)}
      >
        {actionLabel}
      </Button>
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        title={confirmTitle}
        description={confirmDescription}
        confirmLabel={actionLabel}
        destructive
        onConfirm={onConfirm}
        reason={reasonLabel ? { label: reasonLabel } : undefined}
      />
    </div>
  );
}
