"use client";

import { ChevronDownIcon } from "lucide-react";
import * as React from "react";

import { useAppChrome } from "@/app/(app)/apps/[slug]/components/app-chrome-context";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";

const STORAGE_PREFIX = "astrolift.pageshell.header.";

function loadOpen(storageKey: string | undefined, fallback: boolean): boolean {
  if (!storageKey || typeof window === "undefined") return fallback;
  try {
    const raw = window.localStorage.getItem(STORAGE_PREFIX + storageKey);
    return raw === null ? fallback : raw === "1";
  } catch {
    return fallback;
  }
}

function saveOpen(storageKey: string | undefined, open: boolean) {
  if (!storageKey || typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_PREFIX + storageKey, open ? "1" : "0");
  } catch {
    // localStorage may be disabled — degrade silently.
  }
}

interface PageShellProps {
  title: React.ReactNode;
  /** Supporting text or flow content such as breadcrumb navigation. */
  description?: React.ReactNode;
  actions?: React.ReactNode;
  children: React.ReactNode;
  /**
   * Opt-in: render the header's description + actions in a collapsible region
   * so an operator can fold the chrome and hand the viewport to the body
   * (e.g. the run-detail result/log view). The title row stays visible as the
   * fold toggle. Off by default so existing pages are untouched.
   */
  collapsibleHeader?: boolean;
  /** Persists the collapsed choice across reloads when set. */
  headerStorageKey?: string;
  /** Initial state for a collapsible header. Defaults to expanded. */
  defaultHeaderOpen?: boolean;
}

/**
 * Standard chrome for an Astrolift page: hero with title + description
 * + right-aligned actions, then body. Consistent rhythm across surfaces.
 *
 * With `collapsibleHeader`, the description + actions fold away behind the
 * title row so dense detail views can reclaim the header's vertical space.
 */
export function PageShell({
  title,
  description,
  actions,
  children,
  collapsibleHeader = false,
  headerStorageKey,
  defaultHeaderOpen = true,
}: PageShellProps) {
  const { agentShell, framed } = useAppChrome();
  // Inside the agent shell (AgentDetailShell) the page header + BROCS pillar
  // tabs + platform-links row already frame the surface. Render this client's
  // body content-only — drop its own header and outer padding (the shell owns
  // the padding) — so there's a single page chrome, not stacked headers. Keep
  // the content column's `gap-6` rhythm.
  if (agentShell) {
    return <div className="flex flex-1 flex-col gap-6">{children}</div>;
  }
  // Inside the app frame (/apps/[slug]/*) the frame's header names the app
  // and carries the tab row, so the title goes; the page's own description
  // and actions stay, as a toolbar over the body.
  if (framed) {
    return (
      <div className="flex min-w-0 flex-1 flex-col gap-6">
        {(description || actions) && (
          <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            {description ? (
              <div className="text-muted-foreground min-w-0 text-sm [overflow-wrap:anywhere]">
                {description}
              </div>
            ) : (
              <span />
            )}
            {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
          </div>
        )}
        {children}
      </div>
    );
  }
  if (!collapsibleHeader) {
    return (
      <div className="flex min-w-0 flex-1 flex-col gap-6 p-6">
        <header className="flex min-w-0 flex-col gap-2 border-b pb-5 sm:flex-row sm:items-end sm:justify-between">
          <div className="min-w-0">
            <h1 className="text-2xl font-semibold tracking-tight [overflow-wrap:anywhere]">
              {title}
            </h1>
            {description && (
              <div className="text-muted-foreground mt-1 max-w-2xl text-sm [overflow-wrap:anywhere]">
                {description}
              </div>
            )}
          </div>
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </header>
        {children}
      </div>
    );
  }

  return (
    <CollapsibleHeaderShell
      title={title}
      description={description}
      actions={actions}
      storageKey={headerStorageKey}
      defaultOpen={defaultHeaderOpen}
    >
      {children}
    </CollapsibleHeaderShell>
  );
}

function CollapsibleHeaderShell({
  title,
  description,
  actions,
  storageKey,
  defaultOpen,
  children,
}: {
  title: React.ReactNode;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  storageKey?: string;
  defaultOpen: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = React.useState(defaultOpen);
  React.useEffect(() => {
    setOpen(loadOpen(storageKey, defaultOpen));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storageKey]);

  function handleChange(next: boolean) {
    setOpen(next);
    saveOpen(storageKey, next);
  }

  const hasFoldable = Boolean(description || actions);

  return (
    <Collapsible
      open={open}
      onOpenChange={handleChange}
      className="flex flex-1 flex-col gap-6 p-6"
    >
      <header className="border-b pb-5">
        <div className="flex items-start justify-between gap-3">
          <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
          {hasFoldable && (
            <CollapsibleTrigger
              className="group/hdr text-muted-foreground hover:text-foreground -mr-1 mt-1 inline-flex shrink-0 items-center gap-1 rounded-md px-1.5 py-0.5 text-xs transition-colors"
              aria-label={open ? "Collapse header" : "Expand header"}
            >
              <span className="hidden sm:inline">{open ? "Collapse" : "Details"}</span>
              <ChevronDownIcon className="size-4 transition-transform group-data-[state=closed]/hdr:-rotate-90" />
            </CollapsibleTrigger>
          )}
        </div>
        <CollapsibleContent>
          {hasFoldable && (
            <div className="mt-2 flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
              {description && (
                <div className="text-muted-foreground max-w-2xl text-sm">{description}</div>
              )}
              {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
            </div>
          )}
        </CollapsibleContent>
      </header>
      {children}
    </Collapsible>
  );
}
