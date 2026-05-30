"use client";

import { useMutation } from "@apollo/client/react";
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
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { MutationResult } from "@/graphql/identity/identity.types";
import { SET_APP_SUBDOMAIN } from "@/graphql/registry/registry.mutations";
import { GET_APP } from "@/graphql/registry/registry.queries";
import type { ProvisioningStatus } from "@/graphql/registry/registry.types";

import { UrlHealthBadge } from "./url-health-badge";

interface Props {
  appId: string;
  appSlug: string;
  /** Short subdomain label (e.g. `pickup-windows-tool`). Used in the edit field. */
  subdomain: string;
  /**
   * Full platform-managed hostname (e.g. `pickup-windows-tool.astrolift.smdinfra.net`).
   * Computed by the backend from subdomain + ManagedDomain.zone. Empty when no
   * managed domain is configured — falls back to subdomain for display.
   */
  managedHostname: string;
  /** First public workload slug — used to determine routability. */
  primaryWorkloadSlug: string | null;
  /**
   * Provisioning status of the parent app. Used to render the pending
   * URL placeholder (#407 B) when the host isn't routable yet.
   */
  provisioningStatus: ProvisioningStatus;
}

// Primary URL probe is gated on a deployed public workload — the
// subdomain alone has no live origin to probe (DNS may resolve, but
// nothing serves traffic). Showing a probe badge against a
// non-deployed host would always read as "down" and panic operators.

interface SetSubdomainResp {
  setAppSubdomain: MutationResult<{ id: string; slug: string; subdomain: string }>;
}

/**
 * Pending-state badge that replaces UrlHealthBadge while the URL
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
      <span className="inline-flex items-center gap-1 rounded-full border border-amber-300 bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-800 dark:border-amber-700/50 dark:bg-amber-950/30 dark:text-amber-300">
        <Loader2Icon className="size-3 animate-spin" />
        <span>Provisioning · ~2 min</span>
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-zinc-300 bg-zinc-50 px-2 py-0.5 text-xs font-medium text-zinc-600 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300">
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
export function UrlCard({
  appId,
  appSlug,
  subdomain,
  managedHostname,
  primaryWorkloadSlug,
  provisioningStatus,
}: Props) {
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

  const [mutate, { loading: saving }] = useMutation<SetSubdomainResp>(SET_APP_SUBDOMAIN, {
    refetchQueries: [{ query: GET_APP, variables: { slug: appSlug } }],
    awaitRefetchQueries: true,
  });

  // Use the backend-computed managed hostname when available; fall back to the
  // short subdomain label for installs without a managed domain configured.
  const fullHost = managedHostname || subdomain;

  // Routable === backend says provisioning is ready AND there's a
  // public workload to receive traffic. Anything else means the host
  // is either still being brought up or has nothing serving on it yet,
  // and the URL probe would always read as down. Surface a friendlier
  // "provisioning · ~2 min" badge instead. (#407 B)
  const isRoutable = provisioningStatus === "ready" && !!primaryWorkloadSlug;
  const isProvisioning = provisioningStatus === "pending" || provisioningStatus === "provisioning";

  const isDirty = draft.trim() !== subdomain;
  const isValid = SUBDOMAIN_PATTERN.test(draft.trim());
  const canSave = editing && isDirty && isValid && !saving;

  async function handleSave() {
    const next = draft.trim();
    if (!isValid || next === subdomain) return;
    const { data } = await mutate({
      variables: { input: { id: appId, subdomain: next } },
    });
    const env = data?.setAppSubdomain;
    if (!env) {
      toast.error("Subdomain change failed: no response from backend.");
      return;
    }
    if (!env.ok) {
      toast.error(env.errors?.[0]?.message ?? "Subdomain change failed.");
      return;
    }
    toast.success(`Subdomain changed to ${next}.`);
    setEditing(false);
  }

  function handleCancel() {
    setDraft(subdomain);
    setEditing(false);
  }

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(`https://${fullHost}`);
      toast.success("URL copied to clipboard.");
    } catch {
      toast.error("Couldn't copy — clipboard unavailable.");
    }
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-start gap-3 space-y-0">
        <div className="bg-primary/10 text-primary shrink-0 rounded-md p-2.5">
          <GlobeIcon className="size-5" />
        </div>
        <div className="flex-1">
          <CardTitle className="text-base">Public URL</CardTitle>
          <CardDescription className="mt-1">
            The platform-issued host for this app. Edit the subdomain to rename it; DNS and
            certificates re-issue automatically.
          </CardDescription>
        </div>
      </CardHeader>

      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          <a
            href={`https://${fullHost}`}
            target="_blank"
            rel="noreferrer"
            className="text-foreground hover:text-primary inline-flex items-center gap-1.5 font-mono text-sm transition-colors"
          >
            {fullHost}
            <ExternalLinkIcon className="size-3.5" />
          </a>
          {isRoutable ? (
            <UrlHealthBadge appSlug={appSlug} url={`https://${fullHost}/`} />
          ) : (
            <PendingUrlBadge isProvisioning={isProvisioning} hasWorkload={!!primaryWorkloadSlug} />
          )}
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="h-7 gap-1 px-2"
            onClick={handleCopy}
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
                  Press <kbd className="bg-muted rounded px-1 font-mono text-[10px]">Enter</kbd> to
                  save or <kbd className="bg-muted rounded px-1 font-mono text-[10px]">Esc</kbd> to
                  cancel.
                </p>
              )}
            </div>
          ) : (
            <div className="flex items-center justify-between gap-2">
              <div className="space-y-0.5">
                <p className="text-muted-foreground text-xs">Subdomain</p>
                <Badge variant="outline" className="gap-1 font-mono text-xs">
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
      </CardContent>
    </Card>
  );
}
