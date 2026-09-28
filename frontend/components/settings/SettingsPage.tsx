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
 */

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
import { cn } from "@/lib/utils";

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
  className?: string;
}

export function SettingsPage({ sections, dangerZone, readOnly, className }: SettingsPageProps) {
  const nav = [
    ...sections.map((s) => ({ id: s.id, title: s.title })),
    ...(dangerZone ? [{ id: DANGER_ID, title: "Danger zone" }] : []),
  ];
  const [active, setActive] = React.useState(nav[0]?.id);

  const go = (id: string) => {
    setActive(id);
    document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  return (
    <ReadOnlyContext.Provider value={readOnly}>
      <div className={cn("flex min-w-0 flex-col gap-6 md:flex-row md:items-start", className)}>
        <nav
          aria-label="Settings sections"
          className="min-w-0 md:sticky md:top-6 md:w-48 md:shrink-0"
        >
          <div className="md:hidden">
            <Select value={active} onValueChange={go}>
              <SelectTrigger aria-label="Settings section" className="w-full">
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
                <a
                  href={`#${n.id}`}
                  onClick={() => setActive(n.id)}
                  aria-current={active === n.id ? "location" : undefined}
                  className={cn(
                    "block min-w-0 truncate rounded-sm px-2 py-1.5 text-sm transition-colors",
                    active === n.id
                      ? "bg-muted text-foreground font-medium"
                      : "text-muted-foreground hover:text-foreground",
                    n.id === DANGER_ID && "text-danger hover:text-danger"
                  )}
                >
                  {n.title}
                </a>
              </li>
            ))}
          </ul>
        </nav>

        <div className="flex min-w-0 flex-1 flex-col gap-10">
          {readOnly && <ReadOnlyNotice permission={readOnly.permission} />}
          {sections.map((s) => (
            <div key={s.id} id={s.id} className="min-w-0 scroll-mt-6">
              {s.content}
            </div>
          ))}
          {dangerZone && (
            <div id={DANGER_ID} className="min-w-0 scroll-mt-6">
              <Section
                title="Danger zone"
                description="These cannot be undone. Each one asks before it runs."
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

function ReadOnlyNotice({ permission }: { permission: string }) {
  return (
    <p className="bg-muted text-muted-foreground rounded-md border px-3 py-2 text-sm">
      You can view these settings. Changing them needs the{" "}
      <code className="text-foreground font-mono [overflow-wrap:anywhere]">{permission}</code>{" "}
      permission.
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
  saveLabel = "Save",
}: SettingsSectionProps) {
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
                Cancel
              </Button>
            )}
            <Button type="submit" size="sm" disabled={!dirty || saving}>
              {saving ? "Saving…" : saveLabel}
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
}: DangerActionProps) {
  const [open, setOpen] = React.useState(false);
  return (
    <div className="flex min-w-0 flex-col gap-3 py-3 first:pt-0 last:pb-0 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0">
        <p className="text-sm font-medium">{title}</p>
        <p className="text-muted-foreground text-sm [overflow-wrap:anywhere]">{description}</p>
      </div>
      <Button
        type="button"
        variant="destructive"
        size="sm"
        className="shrink-0"
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
