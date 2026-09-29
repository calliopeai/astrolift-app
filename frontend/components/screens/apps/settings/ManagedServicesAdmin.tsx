"use client";

import { Loader2Icon, PencilIcon, PlugIcon, RefreshCwIcon } from "lucide-react";
import * as React from "react";

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

import { configValueToString, type useManagedServicesAdmin } from "./use-managed-services-admin";

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

/**
 * Managed service admin: edit hot-swappable fields in place, or trigger a
 * full re-provision. Hidden while loading and when the app has no live
 * managed services.
 */
export function ManagedServicesAdminView({
  services,
  loading,
  reprovisioning,
  updating,
  onReprovision,
  onSave,
}: ManagedServicesAdminViewProps) {
  const [reprovisionTarget, setReprovisionTarget] = React.useState<AstroliftManagedService | null>(
    null
  );
  const [editTarget, setEditTarget] = React.useState<AstroliftManagedService | null>(null);

  if (loading && services.length === 0) {
    return null;
  }
  if (services.length === 0) {
    return null;
  }

  return (
    <Section
      title={
        <span className="flex items-center gap-2">
          <PlugIcon className="text-muted-foreground size-4 shrink-0" />
          Managed service admin
        </span>
      }
      description="Edit hot-swappable fields in place; trigger a full re-provision when a config change requires tearing down and recreating the backing cloud resource."
    >
      <Card className="overflow-hidden p-0">
        <ul className="divide-y">
          {services.map((svc) => (
            <ManagedServiceAdminRow
              key={svc.id}
              svc={svc}
              onReprovision={() => setReprovisionTarget(svc)}
              onEdit={() => setEditTarget(svc)}
            />
          ))}
        </ul>
      </Card>

      <ReprovisionConfirmDialog
        target={reprovisionTarget}
        loading={reprovisioning}
        onConfirm={onReprovision}
        onOpenChange={(open) => {
          if (!open) setReprovisionTarget(null);
        }}
      />
      <ManagedServiceEditSheet
        target={editTarget}
        loading={updating}
        onSave={onSave}
        onOpenChange={(open) => {
          if (!open) setEditTarget(null);
        }}
      />
    </Section>
  );
}

function ManagedServiceAdminRow({
  svc,
  onReprovision,
  onEdit,
}: {
  svc: AstroliftManagedService;
  onReprovision: () => void;
  onEdit: () => void;
}) {
  const dotStatus = SERVICE_STATUS_VARIANT[svc.status] ?? "muted";
  const editableCount = svc.editableFields?.length ?? 0;
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
            {svc.status}
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
                disabled={editableCount === 0 || inFlight}
              >
                <PencilIcon className="size-3.5" />
                Edit
                {editableCount > 0 ? (
                  <Badge variant="secondary" className="text-2xs ml-1">
                    {editableCount}
                  </Badge>
                ) : null}
              </Button>
            </TooltipTrigger>
            <TooltipContent className="max-w-xs">
              <p className="text-xs">
                {editableCount === 0
                  ? "No fields are editable in place for this kind. Use Re-provision to apply a config change that requires a teardown."
                  : `Edit hot-swappable fields without tearing down the backing resource.`}
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
                disabled={inFlight}
              >
                <RefreshCwIcon className="size-3.5" />
                Re-provision
              </Button>
            </TooltipTrigger>
            <TooltipContent className="max-w-xs">
              <p className="text-xs">
                Tear down and recreate the cloud resource. Use when a config change is not in the
                editable-fields set.
              </p>
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
  onConfirm,
  onOpenChange,
}: {
  target: AstroliftManagedService | null;
  loading: boolean;
  onConfirm: (target: AstroliftManagedService) => Promise<boolean>;
  onOpenChange: (open: boolean) => void;
}) {
  async function handleConfirm() {
    if (!target) return;
    if (await onConfirm(target)) onOpenChange(false);
  }

  return (
    <AlertDialog open={target !== null} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>
            Re-provision {target ? `${target.kind}/${target.name}` : "managed service"}?
          </AlertDialogTitle>
          <AlertDialogDescription>
            The driver tears down the backing cloud resource and recreates it from the current
            config. The service transitions to <span className="font-mono">pending</span>; the
            lifecycle workflow picks it up. Persistent data on this kind may or may not survive the
            teardown — check the kind&apos;s driver docs before confirming.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={loading}>Cancel</AlertDialogCancel>
          <AlertDialogAction
            disabled={loading}
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
            Re-provision
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

function ManagedServiceEditSheet({
  target,
  loading,
  onSave,
  onOpenChange,
}: {
  target: AstroliftManagedService | null;
  loading: boolean;
  onSave: (target: AstroliftManagedService, values: Record<string, string>) => Promise<boolean>;
  onOpenChange: (open: boolean) => void;
}) {
  const [values, setValues] = React.useState<Record<string, string>>({});

  React.useEffect(() => {
    if (!target) {
      setValues({});
      return;
    }
    const editable = target.editableFields ?? [];
    const initial: Record<string, string> = {};
    for (const field of editable) {
      initial[field] = configValueToString(target.config?.[field]);
    }
    setValues(initial);
  }, [target]);

  async function handleSave() {
    if (!target) return;
    if (await onSave(target, values)) onOpenChange(false);
  }

  const editable = target?.editableFields ?? [];

  return (
    <Sheet open={target !== null} onOpenChange={onOpenChange}>
      <SheetContent className="flex flex-col gap-0 sm:max-w-md">
        <SheetHeader>
          <SheetTitle className="flex items-center gap-2">
            <PencilIcon className="size-4" />
            {target ? `Edit ${target.kind}/${target.name}` : "Edit managed service"}
          </SheetTitle>
          <SheetDescription>
            Only hot-swappable fields are shown. Changes apply via the driver&apos;s update path —
            no teardown. For other changes, use Re-provision.
          </SheetDescription>
        </SheetHeader>

        <div className="flex-1 space-y-4 overflow-y-auto p-4">
          {target && editable.length === 0 ? (
            <div className="bg-muted/40 rounded-md border p-3 text-xs">
              <p className="text-muted-foreground">
                This kind exposes no editable fields. Edit via the manifest TOML editor and
                re-provision, or change here once the driver adds in-place support.
              </p>
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
                onChange={(e) => setValues((v) => ({ ...v, [field]: e.target.value }))}
                className="font-mono text-xs"
              />
            </div>
          ))}
        </div>

        <SheetFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={loading}>
            Cancel
          </Button>
          <Can permission="app.deploy">
            <Button onClick={() => void handleSave()} disabled={loading || editable.length === 0}>
              {loading ? <Loader2Icon className="size-4 animate-spin" /> : null}
              Save changes
            </Button>
          </Can>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
