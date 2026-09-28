"use client";

import {
  AlertTriangleIcon,
  BoxIcon,
  ChevronRightIcon,
  DatabaseIcon,
  KeyIcon,
  Loader2Icon,
  LockIcon,
  RefreshCwIcon,
  ShieldIcon,
  Trash2Icon,
  WebhookIcon,
} from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Can } from "@/components/Can";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";

import type { useDangerZone } from "./use-danger-zone";

export type DangerZoneViewProps = ReturnType<typeof useDangerZone>;

interface ResourceGroupSpec {
  key: string;
  icon: typeof BoxIcon;
  count: number;
  body: React.ReactNode;
}

function ResourceGroup({ group, labelKey }: { group: ResourceGroupSpec; labelKey: string }) {
  const t = useTranslations("apps.settings.dangerZone.preview.groups");
  const [open, setOpen] = React.useState(false);
  const Icon = group.icon;
  if (group.count <= 0) return null;
  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger asChild>
        <button
          type="button"
          className="hover:bg-muted/60 flex w-full items-center gap-2 rounded-md border bg-transparent px-2 py-1.5 text-left text-xs"
        >
          <ChevronRightIcon
            className={`size-3.5 transition-transform ${open ? "rotate-90" : ""}`}
          />
          <Icon className="text-muted-foreground size-4" />
          <span className="text-foreground flex-1 font-medium">{t(labelKey)}</span>
          <Badge variant="secondary" className="text-2xs">
            {group.count}
          </Badge>
        </button>
      </CollapsibleTrigger>
      <CollapsibleContent className="mt-1 pl-7">{group.body}</CollapsibleContent>
    </Collapsible>
  );
}

/**
 * Hard-deregister + full teardown (#392 + #436 A/B/C), a row of the
 * Settings tab's Danger zone (spec 44 §5.3).
 *
 * The confirm stays an AlertDialog of its own rather than ConfirmDialog:
 * it carries the blast-radius preview and a type-the-name guard, and
 * ConfirmDialog has room for neither.
 *
 * The destructive button stays disabled until the operator types the
 * app's name verbatim (muscle-memory guard) — the backend enforces
 * the same check server-side via the `confirm_name` field on
 * `DeregisterAppInput`.
 *
 * #436 A — blast-radius preview: the modal fetches
 * `previewAstroliftDeregister` on open and renders an expandable
 * grouped list (k8s / managed-services / identity / network / secrets)
 * with the actual object names the workflow will tear down. The
 * trigger button carries a resource-count badge so the operator sees
 * the magnitude before clicking through.
 *
 * #436 C — partial-failure resume: the still-live banner gets an
 * inline Retry CTA that fires the deregister mutation directly
 * (same workflow id → Temporal de-dup joins the existing run). No
 * modal reopen, no re-type.
 */
export function DangerZoneView({
  appName,
  loading,
  preview,
  previewLoading,
  stillLive,
  loadPreview,
  onDeregister,
  onRetry,
}: DangerZoneViewProps) {
  const t = useTranslations("apps.settings.dangerZone");
  const tPreview = useTranslations("apps.settings.dangerZone.preview");
  const [open, setOpen] = React.useState(false);
  const [confirm, setConfirm] = React.useState("");

  // Lazy-load the preview when the modal opens so closed-modal renders
  // don't fire a network call.
  React.useEffect(() => {
    if (open) {
      loadPreview();
    } else {
      setConfirm("");
    }
  }, [open, loadPreview]);

  const armed = confirm.trim() === appName && !loading;

  // Group the preview rows by destination so the expander tree maps
  // 1:1 with the workflow's per-step ordering (k8s → managed
  // services → identity → secrets → network).
  const k8sBody = React.useMemo(() => {
    if (!preview || preview.k8sObjects.length === 0) return null;
    // Group k8s objects by (cluster, namespace) for readability.
    const groups = new Map<string, typeof preview.k8sObjects>();
    for (const o of preview.k8sObjects) {
      const key = `${o.clusterSlug}/${o.namespace}`;
      const arr = groups.get(key) ?? [];
      arr.push(o);
      groups.set(key, arr);
    }
    return (
      <ul className="text-2xs space-y-2">
        {Array.from(groups.entries()).map(([key, items]) => (
          <li key={key}>
            <p className="text-muted-foreground font-mono">{key}</p>
            <ul className="mt-0.5 list-disc pl-5 font-mono">
              {items.map((o) => (
                <li key={`${key}-${o.apiVersion}-${o.kind}-${o.name}`}>
                  <span className="text-muted-foreground">{o.kind}</span>{" "}
                  <span className="text-foreground">{o.name}</span>
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ul>
    );
  }, [preview]);

  const managedServicesBody = React.useMemo(() => {
    if (!preview || preview.managedServices.length === 0) return null;
    return (
      <ul className="text-2xs space-y-1">
        {preview.managedServices.map((s) => (
          <li key={s.id} className="flex items-center gap-2 font-mono">
            <span className="text-foreground">{s.name || s.kind}</span>
            <Badge variant="outline" className="text-2xs">
              {s.kind}
              {s.variant ? `/${s.variant}` : ""}
            </Badge>
            <span className="text-muted-foreground">{s.environmentName}</span>
          </li>
        ))}
      </ul>
    );
  }, [preview]);

  const secretsBody = React.useMemo(() => {
    if (!preview || preview.secretRefs.length === 0) return null;
    return (
      <ul className="text-2xs space-y-1 font-mono">
        {preview.secretRefs.map((r) => (
          <li key={r.id}>
            <span className="text-foreground">{r.bundleSlug}</span>
            {r.prefix ? <span className="text-muted-foreground"> ({r.prefix})</span> : null}{" "}
            <span className="text-muted-foreground">→ {r.environmentName}</span>
            {r.clusterSlug ? (
              <span className="text-muted-foreground"> @ {r.clusterSlug}</span>
            ) : null}
          </li>
        ))}
      </ul>
    );
  }, [preview]);

  const tokensBody = React.useMemo(() => {
    if (!preview || preview.deployTokens.length === 0) return null;
    return (
      <ul className="text-2xs space-y-1 font-mono">
        {preview.deployTokens.map((tok) => (
          <li key={tok.id}>
            <span className="text-foreground">{tok.name}</span>
            <span className="text-muted-foreground"> ····{tok.last4}</span>
          </li>
        ))}
      </ul>
    );
  }, [preview]);

  const identityBody = React.useMemo(() => {
    if (!preview || preview.identityRoles.length === 0) return null;
    return (
      <ul className="text-2xs space-y-1 font-mono">
        {preview.identityRoles.map((r) => (
          <li key={`${r.clusterSlug}-${r.roleArnOrPrincipal}`}>
            <span className="text-muted-foreground">{r.clusterSlug}</span>{" "}
            <span className="text-foreground">{r.roleArnOrPrincipal}</span>
            <Badge variant="outline" className="text-2xs ml-1">
              {r.kind}
            </Badge>
          </li>
        ))}
      </ul>
    );
  }, [preview]);

  const networkBody = React.useMemo(() => {
    if (!preview) return null;
    const hasWebhook = preview.sourceWebhook?.installed === true;
    const hasRegistry = !!preview.registryRepoUri;
    if (!hasWebhook && !hasRegistry) return null;
    return (
      <ul className="text-2xs space-y-1 font-mono">
        {hasWebhook && preview.sourceWebhook ? (
          <li>
            <span className="text-muted-foreground">{tPreview("sourceWebhookLabel")} →</span>{" "}
            <span className="text-foreground">{preview.sourceWebhook.repo}</span>
            <span className="text-muted-foreground"> #{preview.sourceWebhook.hookId}</span>
          </li>
        ) : null}
        {hasRegistry ? (
          <li>
            <span className="text-muted-foreground">{tPreview("registryRepoLabel")} →</span>{" "}
            <span className="text-foreground break-all">{preview.registryRepoUri}</span>
          </li>
        ) : null}
      </ul>
    );
  }, [preview, tPreview]);

  const groups: ResourceGroupSpec[] = preview
    ? [
        { key: "k8s", icon: BoxIcon, count: preview.k8sObjects.length, body: k8sBody },
        {
          key: "managedServices",
          icon: DatabaseIcon,
          count: preview.managedServices.length,
          body: managedServicesBody,
        },
        {
          key: "secrets",
          icon: LockIcon,
          count: preview.secretRefs.length,
          body: secretsBody,
        },
        {
          key: "deployTokens",
          icon: KeyIcon,
          count: preview.deployTokens.length,
          body: tokensBody,
        },
        {
          key: "identity",
          icon: ShieldIcon,
          count: preview.identityRoles.length,
          body: identityBody,
        },
        {
          key: "network",
          icon: WebhookIcon,
          count: (preview.sourceWebhook?.installed ? 1 : 0) + (preview.registryRepoUri ? 1 : 0),
          body: networkBody,
        },
      ]
    : [];

  async function handleConfirm() {
    if (!armed) return;
    if (await onDeregister(confirm.trim())) setOpen(false);
  }

  async function handleRetry() {
    if (await onRetry()) setOpen(false);
  }

  return (
    <>
      <div className="flex min-w-0 flex-col gap-3 py-3 first:pt-0 last:pb-0">
        <div className="flex min-w-0 flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0">
            <p className="text-sm font-medium">{t("button")}</p>
            <p className="text-muted-foreground text-sm [overflow-wrap:anywhere]">
              {t("description")}
            </p>
          </div>
          <Can permission="app.delete">
            <Button
              type="button"
              variant="destructive"
              size="sm"
              className="shrink-0"
              onClick={() => setOpen(true)}
            >
              <Trash2Icon className="size-3.5" />
              {t("button")}
              {preview && preview.totalResourceCount > 0 ? (
                <Badge variant="secondary" className="text-2xs ml-1.5 font-mono">
                  {preview.totalResourceCount}
                </Badge>
              ) : null}
            </Button>
          </Can>
        </div>
        {stillLive.length > 0 ? (
          <div className="border-warning-border bg-warning/5 min-w-0 rounded-md border p-3 text-xs">
            <p className="text-warning-fg">{t("stillLive", { count: stillLive.length })}</p>
            <ul className="text-foreground text-2xs mt-1 list-disc pl-5 font-mono [overflow-wrap:anywhere]">
              {stillLive.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
            <Can permission="app.delete">
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="mt-2"
                onClick={() => void handleRetry()}
                disabled={loading}
              >
                {loading ? (
                  <Loader2Icon className="size-4 animate-spin" />
                ) : (
                  <RefreshCwIcon className="size-4" />
                )}
                {t("retry")}
              </Button>
            </Can>
          </div>
        ) : (
          <p className="text-muted-foreground text-2xs">{t("softHint")}</p>
        )}
      </div>

      <AlertDialog open={open} onOpenChange={setOpen}>
        <AlertDialogContent className="max-h-[90vh] overflow-y-auto">
          <AlertDialogHeader>
            <AlertDialogTitle className="flex items-center gap-2">
              <AlertTriangleIcon className="text-destructive size-5" />
              {t("confirmTitle", { name: appName })}
            </AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="space-y-3 text-sm">
                <p>{t("confirmIntro")}</p>

                {/* Blast-radius preview (#436 A) — collapsed by
                    default. Each group expands to the actual object
                    names the workflow will tear down. */}
                <div className="bg-muted/40 space-y-2 rounded-md border p-3">
                  <p className="text-xs font-medium">
                    {previewLoading
                      ? tPreview("loading")
                      : tPreview("header", {
                          count: preview?.totalResourceCount ?? 0,
                        })}
                  </p>
                  {previewLoading ? (
                    <Skeleton className="h-16 w-full" />
                  ) : preview && preview.totalResourceCount > 0 ? (
                    <div className="space-y-1.5">
                      {groups.map((g) => (
                        <ResourceGroup key={g.key} group={g} labelKey={g.key} />
                      ))}
                    </div>
                  ) : (
                    <p className="text-muted-foreground text-xs">{tPreview("noResources")}</p>
                  )}
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="deregister-confirm" className="text-xs">
                    {t("typeToConfirm")} <span className="font-mono">{appName}</span>
                  </Label>
                  <Input
                    id="deregister-confirm"
                    autoComplete="off"
                    autoCorrect="off"
                    spellCheck={false}
                    value={confirm}
                    onChange={(e) => setConfirm(e.target.value)}
                    disabled={loading}
                  />
                </div>
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={loading}>{t("cancel")}</AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                e.preventDefault();
                void handleConfirm();
              }}
              disabled={!armed}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {loading ? (
                <Loader2Icon className="size-4 animate-spin" />
              ) : (
                <Trash2Icon className="size-4" />
              )}
              {loading ? t("deregistering") : t("button")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
