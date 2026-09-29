"use client";

import {
  CheckIcon,
  ClockIcon,
  CopyIcon,
  ExternalLinkIcon,
  GlobeIcon,
  Loader2Icon,
  PencilIcon,
  XIcon,
} from "lucide-react";
import * as React from "react";

import { Can } from "@/components/Can";
import { Panel, type PanelSpan } from "@/components/panel/Panel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

import type { useUrlCard } from "./use-url-card";

export type UrlCardViewProps = ReturnType<typeof useUrlCard> & {
  /**
   * The live health pill for the public URL, shown only when the host is
   * routable (UrlHealthBadge polls, so it is a slot and mounts only then).
   */
  healthBadge?: React.ReactNode;
  span?: PanelSpan;
};

/**
 * Pending-state badge that replaces the health pill while the URL
 * isn't routable yet (#407 B). Two shapes:
 *
 *  * `isProvisioning` — backend is still bringing the app up. Amber
 *    chip + spinning indicator + "~2 min" hint operators have come
 *    to expect for a fresh app.
 *  * Otherwise (ready but no public workload) — neutral chip with
 *    "first deploy will publish it" copy. The card historically just
 *    hid the URL row here; surfacing this badge instead removes the
 *    "why is there nothing?" confusion.
 */
function PendingUrlBadge({
  isProvisioning,
  hasWorkload,
}: {
  isProvisioning: boolean;
  hasWorkload: boolean;
}) {
  if (isProvisioning) {
    return (
      <span className="border-warning-border bg-warning/10 text-warning-fg inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium">
        <Loader2Icon className="size-3 animate-spin" />
        <span>Provisioning · ~2 min</span>
      </span>
    );
  }
  return (
    <span className="border-border bg-muted text-muted-foreground inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium">
      <ClockIcon className="size-3" />
      <span>
        {hasWorkload
          ? "URL not yet routable"
          : "URL not yet assigned — first deploy will publish it"}
      </span>
    </span>
  );
}

// Full hostname pattern: 1..n DNS labels separated by dots. Each
// label: lowercase alphanumeric + hyphen, no leading/trailing hyphen,
// max 63 chars. Total length 1..253.
const SUBDOMAIN_PATTERN =
  /^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)(\.([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?))*$/;

/**
 * Primary URL card for the app overview (#408). Renders the
 * platform-issued public host and exposes an inline edit affordance
 * for the subdomain. The mutation (`setAppSubdomain`) is gated on
 * `app.update`; viewers without it get a read-only card.
 *
 * The "primary host" shown here is the first public workload —
 * matches the topology's ingress node and the page-header Open
 * button. When no public workload exists we still render the
 * subdomain so operators can rename it before deploying.
 */
export function UrlCardView({
  subdomain,
  fullHost,
  isRoutable,
  isProvisioning,
  hasWorkload,
  saving,
  onSave,
  onCopy,
  healthBadge,
  span = 6,
}: UrlCardViewProps) {
  const [editing, setEditing] = React.useState(false);
  const [draft, setDraft] = React.useState(subdomain);
  const inputRef = React.useRef<HTMLInputElement | null>(null);

  // Keep the draft in sync with the server value when the user is
  // not actively editing — e.g. another tab renames the subdomain
  // while this card is open. (Matches the pattern used in
  // config-editor-client; the lint warning is accepted.)
  React.useEffect(() => {
    if (!editing) setDraft(subdomain);
  }, [editing, subdomain]);

  React.useEffect(() => {
    if (editing) {
      // Focus the input on enter-edit. requestAnimationFrame avoids
      // races with the conditional render.
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [editing]);

  const isDirty = draft.trim() !== subdomain;
  const isValid = SUBDOMAIN_PATTERN.test(draft.trim());
  const canSave = editing && isDirty && isValid && !saving;

  async function handleSave() {
    const next = draft.trim();
    if (!isValid || next === subdomain) return;
    if (await onSave(next)) setEditing(false);
  }

  function handleCancel() {
    setDraft(subdomain);
    setEditing(false);
  }

  return (
    <Panel
      title="Public URL"
      icon={<GlobeIcon className="size-4" />}
      description="The platform-issued host for this app. Edit the subdomain to rename it; DNS and certificates re-issue automatically."
      span={span}
    >
      <div className="min-w-0 space-y-4">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <a
            href={`https://${fullHost}`}
            target="_blank"
            rel="noreferrer"
            className="text-foreground hover:text-primary inline-flex min-w-0 items-center gap-1.5 font-mono text-sm [overflow-wrap:anywhere] transition-colors"
          >
            <span className="min-w-0">{fullHost}</span>
            <ExternalLinkIcon className="size-3.5 shrink-0" />
          </a>
          {isRoutable ? (
            healthBadge
          ) : (
            <PendingUrlBadge isProvisioning={isProvisioning} hasWorkload={hasWorkload} />
          )}
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="h-7 gap-1 px-2"
            onClick={onCopy}
          >
            <CopyIcon className="size-3.5" />
            <span className="text-xs">Copy</span>
          </Button>
        </div>

        <Can
          permission="app.update"
          fallback={
            <p className="text-muted-foreground text-xs italic">
              You need the <span className="font-mono">app.update</span> permission to rename this
              subdomain.
            </p>
          }
        >
          {editing ? (
            <div className="space-y-2">
              <Label htmlFor="subdomain-input" className="text-xs">
                Subdomain
              </Label>
              <div className="flex flex-wrap items-stretch gap-2">
                <Input
                  id="subdomain-input"
                  ref={inputRef}
                  value={draft}
                  onChange={(e) => setDraft(e.target.value.toLowerCase())}
                  disabled={saving}
                  aria-invalid={!isValid}
                  placeholder="my-app.acme.astrolift.app"
                  className="flex-1 font-mono text-sm"
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && canSave) {
                      e.preventDefault();
                      void handleSave();
                    } else if (e.key === "Escape") {
                      e.preventDefault();
                      handleCancel();
                    }
                  }}
                />
                <div className="flex gap-2">
                  <Button
                    type="button"
                    size="sm"
                    onClick={handleSave}
                    disabled={!canSave}
                    className="gap-1"
                  >
                    {saving ? (
                      <Loader2Icon className="size-4 animate-spin" />
                    ) : (
                      <CheckIcon className="size-4" />
                    )}
                    Save
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={handleCancel}
                    disabled={saving}
                    className="gap-1"
                  >
                    <XIcon className="size-4" />
                    Cancel
                  </Button>
                </div>
              </div>
              {!isValid && draft.length > 0 ? (
                <p className="text-destructive text-xs">
                  Lowercase DNS host: letters, digits, hyphens, and dots. Each label must start and
                  end with a letter or digit.
                </p>
              ) : (
                <p className="text-muted-foreground text-xs">
                  Press <kbd className="bg-muted text-2xs rounded px-1 font-mono">Enter</kbd> to
                  save or <kbd className="bg-muted text-2xs rounded px-1 font-mono">Esc</kbd> to
                  cancel.
                </p>
              )}
            </div>
          ) : (
            <div className="flex min-w-0 items-center justify-between gap-2">
              <div className="min-w-0 space-y-0.5">
                <p className="text-muted-foreground text-xs">Subdomain</p>
                <Badge
                  variant="outline"
                  className="h-auto max-w-full gap-1 font-mono text-xs [overflow-wrap:anywhere] whitespace-normal"
                >
                  {subdomain}
                </Badge>
              </div>
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => setEditing(true)}
                className="gap-1"
              >
                <PencilIcon className="size-3.5" />
                Edit
              </Button>
            </div>
          )}
        </Can>
      </div>
    </Panel>
  );
}
