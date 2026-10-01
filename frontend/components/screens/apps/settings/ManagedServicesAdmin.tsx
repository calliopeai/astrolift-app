"use client";

import { Loader2Icon, PencilIcon, PlugIcon, RefreshCwIcon } from "lucide-react";
import * as React from "react";
import { useTranslations } from "next-intl";

import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

import { Can } from "@/components/Can";
import { StatusDot } from "@/components/StatusDot";
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
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Section } from "@/components/ui/section";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { AstroliftManagedService } from "@/graphql/services/services.types";

import {
  configValueToString,
  configValue,
  editableConfigKeys,
  managedServiceObservation,
  type useManagedServicesAdmin,
} from "./use-managed-services-admin";

export type ManagedServicesAdminViewProps = ReturnType<typeof useManagedServicesAdmin>;

const SERVICE_STATUS_VARIANT: Record<string, "ok" | "warn" | "error" | "pending" | "muted"> = {
  active: "ok",
  pending: "warn",
  provisioning: "pending",
  updating: "pending",
  deprovisioning: "pending",
  failed: "error",
  deleted: "muted",
};

type Review = {
  kind: "edit" | "reprovision";
  service: AstroliftManagedService;
  source: string;
  scope: number;
};

/** Local reviews are invalidated by observed target/authority/config changes, not server CAS. */
export function ManagedServicesAdminView({
  services,
  loading,
  reprovisioning,
  updating,
  onReprovision,
  onSave,
  target = "",
  error = null,
  onRetry,
}: ManagedServicesAdminViewProps) {
  const t = useTranslations("apps.managedServicesAdmin");
  const perms = useMyPermissions();
  // Keep Can's existing optimistic loading policy and exact app.deploy gate.
  const allowed = (perms.loading && perms.granted.size === 0) || perms.can("app.deploy");
  const fingerprint = JSON.stringify([target, allowed]);
  const [scope, setScope] = React.useState({ fingerprint, serial: 0 });
  if (scope.fingerprint !== fingerprint) setScope({ fingerprint, serial: scope.serial + 1 });
  const [review, setReview] = React.useState<Review | null>(null);
  if (
    review &&
    (review.scope !== scope.serial ||
      !services.some(
        (s) => s.id === review.service.id && managedServiceObservation(s) === review.source
      ))
  )
    setReview(null);
  const currentReview = React.useRef<Review | null>(null);
  const currentWritable = React.useRef(false);
  const writable = allowed && !error && !loading;
  React.useLayoutEffect(() => {
    currentReview.current = review;
    currentWritable.current = writable;
    return () => {
      currentReview.current = null;
      currentWritable.current = false;
    };
  }, [review, writable]);
  function open(kind: Review["kind"], service: AstroliftManagedService) {
    if (!currentWritable.current) return;
    const next = {
      kind,
      service: JSON.parse(JSON.stringify(service)) as AstroliftManagedService,
      source: managedServiceObservation(service),
      scope: scope.serial,
    };
    currentReview.current = next;
    setReview(next);
  }
  function close() {
    if (currentReview.current !== review) return;
    currentReview.current = null;
    setReview(null);
  }
  async function save(service: AstroliftManagedService, values: Record<string, string>) {
    if (currentReview.current !== review || !currentWritable.current) return false;
    return onSave(service, values);
  }
  async function reprovision(service: AstroliftManagedService) {
    if (currentReview.current !== review || !currentWritable.current) return false;
    return onReprovision(service);
  }
  if (!loading && !error && services.length === 0) return null;
  return (
    <Section
      title={
        <span className="flex items-center gap-2">
          <PlugIcon className="text-muted-foreground size-4 shrink-0" />
          {t("title")}
        </span>
      }
      description={t("description")}
    >
      {loading && (
        <p role="status" className="text-muted-foreground text-xs">
          {t("loading")}
        </p>
      )}
      {error && (
        <div role="alert" className="space-y-2">
          <p>{t("readFailed")}</p>
          {error !== t("readFailed") && (
            <p className="font-mono text-xs [overflow-wrap:anywhere]">{error}</p>
          )}
          {onRetry && (
            <Button type="button" size="sm" variant="outline" onClick={onRetry}>
              {t("retry")}
            </Button>
          )}
        </div>
      )}
      {services.length > 0 && (
        <Card className="overflow-hidden p-0">
          <ul className="divide-y">
            {services.map((svc) => (
              <ManagedServiceAdminRow
                key={svc.id}
                svc={svc}
                disabled={!writable}
                onReprovision={() => open("reprovision", svc)}
                onEdit={() => open("edit", svc)}
              />
            ))}
          </ul>
        </Card>
      )}
      <ReprovisionConfirmDialog
        target={review?.kind === "reprovision" ? review.service : null}
        loading={reprovisioning}
        disabled={!writable}
        onConfirm={reprovision}
        error={error}
        onRetry={onRetry}
        onOpenChange={(open) => {
          if (!open) close();
        }}
      />
      {review?.kind === "edit" && (
        <ManagedServiceEditSheet
          key={review.source + ":" + review.scope}
          target={review.service}
          loading={updating}
          disabled={!writable}
          onSave={save}
          error={error}
          onRetry={onRetry}
          onOpenChange={(open) => {
            if (!open) close();
          }}
        />
      )}
    </Section>
  );
}

function ManagedServiceAdminRow({
  svc,
  disabled,
  onReprovision,
  onEdit,
}: {
  svc: AstroliftManagedService;
  disabled: boolean;
  onReprovision: () => void;
  onEdit: () => void;
}) {
  const t = useTranslations("apps.managedServicesAdmin");
  const knownStatus = Object.hasOwn(SERVICE_STATUS_VARIANT, svc.status);
  const dotStatus = knownStatus ? SERVICE_STATUS_VARIANT[svc.status] : "muted";
  const editableCount = editableConfigKeys(svc).length;
  const inFlight = ["provisioning", "updating", "deprovisioning", "pending"].includes(svc.status);

  return (
    <li className="flex items-center gap-3 px-4 py-3">
      <StatusDot status={dotStatus} />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-1.5">
          <span className="text-foreground font-mono text-sm">{svc.name || svc.kind}</span>
          <Badge variant="secondary" className="text-2xs">
            {svc.kind}
            {svc.variant ? `/${svc.variant}` : ""}
          </Badge>
          <Badge variant="outline" className="text-2xs font-mono">
            {svc.environmentName}
          </Badge>
          <Badge variant="outline" className="text-2xs capitalize">
            {knownStatus ? t(`status.${svc.status}`) : svc.status}
          </Badge>
        </div>
        {svc.statusError ? (
          <p className="text-destructive text-2xs mt-0.5 max-w-md truncate">{svc.statusError}</p>
        ) : null}
      </div>
      <div className="flex shrink-0 items-center gap-1">
        <Can permission="app.deploy">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="outline"
                size="sm"
                className="h-8"
                onClick={onEdit}
                disabled={disabled || editableCount === 0 || inFlight}
              >
                <PencilIcon className="size-3.5" />
                {t("edit")}
                {editableCount > 0 ? (
                  <Badge variant="secondary" className="text-2xs ml-1">
                    {editableCount}
                  </Badge>
                ) : null}
              </Button>
            </TooltipTrigger>
            <TooltipContent className="max-w-xs">
              <p className="text-xs">
                {editableCount === 0 ? t("noEditableTooltip") : t("editTooltip")}
              </p>
            </TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="outline"
                size="sm"
                className="h-8"
                onClick={onReprovision}
                disabled={disabled || inFlight}
              >
                <RefreshCwIcon className="size-3.5" />
                {t("reprovision")}
              </Button>
            </TooltipTrigger>
            <TooltipContent className="max-w-xs">
              <p className="text-xs">{t("reprovisionTooltip")}</p>
            </TooltipContent>
          </Tooltip>
        </Can>
      </div>
    </li>
  );
}

function ReprovisionConfirmDialog({
  target,
  loading,
  disabled,
  error,
  onRetry,
  onConfirm,
  onOpenChange,
}: {
  target: AstroliftManagedService | null;
  loading: boolean;
  disabled: boolean;
  error: string | null;
  onRetry?: () => void;
  onConfirm: (target: AstroliftManagedService) => Promise<boolean>;
  onOpenChange: (open: boolean) => void;
}) {
  const t = useTranslations("apps.managedServicesAdmin");
  async function handleConfirm() {
    if (!target || disabled || loading) return;
    if (await onConfirm(target)) onOpenChange(false);
  }

  return (
    <AlertDialog open={target !== null} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>
            {t("reprovisionTitle", {
              name: target ? `${target.kind}/${target.name}` : t("service"),
            })}
          </AlertDialogTitle>
          <AlertDialogDescription>{t("reprovisionDescription")}</AlertDialogDescription>
        </AlertDialogHeader>
        <ReviewReadFailure error={error} onRetry={onRetry} />
        <AlertDialogFooter>
          <AlertDialogCancel disabled={loading}>{t("cancel")}</AlertDialogCancel>
          <AlertDialogAction
            disabled={loading || disabled}
            onClick={(e) => {
              e.preventDefault();
              void handleConfirm();
            }}
          >
            {loading ? (
              <Loader2Icon className="size-4 animate-spin" />
            ) : (
              <RefreshCwIcon className="size-4" />
            )}
            {t("reprovision")}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

function ManagedServiceEditSheet({
  target,
  loading,
  disabled,
  error,
  onRetry,
  onSave,
  onOpenChange,
}: {
  target: AstroliftManagedService | null;
  loading: boolean;
  disabled: boolean;
  error: string | null;
  onRetry?: () => void;
  onSave: (target: AstroliftManagedService, values: Record<string, string>) => Promise<boolean>;
  onOpenChange: (open: boolean) => void;
}) {
  const t = useTranslations("apps.managedServicesAdmin");
  const editable = target ? editableConfigKeys(target) : [];
  const [values, setValues] = React.useState<Record<string, string>>(() =>
    Object.fromEntries(
      editable.map((field) => [
        field,
        configValueToString(target ? configValue(target, field) : undefined),
      ])
    )
  );
  const revision = React.useRef(0);
  async function handleSave() {
    if (!target || disabled || loading) return;
    const approvedRevision = revision.current;
    if (await onSave(target, values)) {
      if (revision.current === approvedRevision) onOpenChange(false);
    }
  }

  return (
    <Sheet open={target !== null} onOpenChange={onOpenChange}>
      <SheetContent className="flex flex-col gap-0 sm:max-w-md">
        <SheetHeader>
          <SheetTitle className="flex items-center gap-2">
            <PencilIcon className="size-4" />
            {t("editTitle", { name: target ? `${target.kind}/${target.name}` : t("service") })}
          </SheetTitle>
          <SheetDescription>{t("editDescription")}</SheetDescription>
        </SheetHeader>

        <div className="flex-1 space-y-4 overflow-y-auto p-4">
          <ReviewReadFailure error={error} onRetry={onRetry} />
          {target && editable.length === 0 ? (
            <div className="bg-muted/40 rounded-md border p-3 text-xs">
              <p className="text-muted-foreground">{t("noEditable")}</p>
            </div>
          ) : null}
          {editable.map((field) => (
            <div key={field} className="grid gap-1.5">
              <Label htmlFor={`msvc-edit-${field}`} className="font-mono text-xs">
                {field}
              </Label>
              <Input
                id={`msvc-edit-${field}`}
                value={values[field] ?? ""}
                onChange={(e) => {
                  revision.current++;
                  setValues((v) => ({ ...v, [field]: e.target.value }));
                }}
                className="font-mono text-xs"
              />
              <p className="text-muted-foreground text-xs">
                {t(
                  typeof configValue(target!, field) === "number"
                    ? "numberHint"
                    : typeof configValue(target!, field) === "boolean"
                      ? "booleanHint"
                      : "textHint"
                )}
              </p>
            </div>
          ))}
        </div>

        <SheetFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={loading}>
            {t("cancel")}
          </Button>
          <Can permission="app.deploy">
            <Button
              onClick={() => void handleSave()}
              disabled={disabled || loading || editable.length === 0}
            >
              {loading ? <Loader2Icon className="size-4 animate-spin" /> : null}
              {t("save")}
            </Button>
          </Can>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}

/** A retained modal must expose the real read retry without requiring dismissal. */
function ReviewReadFailure({ error, onRetry }: { error: string | null; onRetry?: () => void }) {
  const t = useTranslations("apps.managedServicesAdmin");
  if (!error) return null;
  return (
    <div role="alert" className="space-y-2">
      <p>{t("readFailed")}</p>
      {error !== t("readFailed") && (
        <p className="font-mono text-xs [overflow-wrap:anywhere]">{error}</p>
      )}
      {onRetry && (
        <Button type="button" variant="outline" size="sm" onClick={onRetry}>
          {t("retry")}
        </Button>
      )}
    </div>
  );
}
