"use client";

import { DatabaseIcon, InfoIcon, MailIcon, PlusIcon, Trash2Icon } from "lucide-react";
import * as React from "react";

import { Can } from "@/components/Can";
import { DataTable, type Column } from "@/components/data-table";
import { PageShell } from "@/components/PageShell";
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
import { Card, CardContent } from "@/components/ui/card";
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
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import type { AstroliftAppEnvironment } from "@/graphql/lifecycle/lifecycle.types";

import type { ManagedService, ProvisionInput, useManagedServices } from "./use-managed-services";

const STATUS_DOT: Record<string, "ok" | "warn" | "error" | "pending" | "muted"> = {
  active: "ok",
  pending: "warn",
  provisioning: "pending",
  updating: "pending",
  deprovisioning: "pending",
  failed: "error",
  deleted: "muted",
};

const KIND_OPTIONS = ["postgres", "redis", "s3", "sqs", "mysql", "kafka"];

// #663 — kind-specific validation surface. The badges flag the "is this
// service actually safe / configured" facts that aren't already visible
// from the lifecycle status alone (e.g. an `active` SES service can still
// be sending un-DKIMed email; an `active` S3 bucket can be wide-open).
//
// Missing config keys render nothing — the JSON is sparse and we want the
// row to stay quiet rather than nag with "not configured" when the driver
// hasn't yet populated the field.

const GREEN_BADGE = "border-success-border bg-success/15 text-success-fg";
const AMBER_BADGE = "border-warning-border bg-warning/15 text-warning-fg";
const RED_BADGE = "border-destructive/40 bg-destructive/15 text-destructive";

function ValidationBadges({ service }: { service: ManagedService }) {
  const cfg = service.config ?? {};
  const items: React.ReactNode[] = [];

  switch (service.kind) {
    case "ses_email": {
      if ("dkim_verified" in cfg) {
        const verified = cfg.dkim_verified === true;
        items.push(
          <Badge key="dkim" variant="outline" className={verified ? GREEN_BADGE : AMBER_BADGE}>
            {verified ? "DKIM verified" : "DKIM pending"}
          </Badge>
        );
      } else {
        items.push(
          <Badge key="dkim" variant="outline" className={AMBER_BADGE}>
            DKIM pending
          </Badge>
        );
      }
      if (typeof cfg.domain_status === "string" && cfg.domain_status) {
        items.push(
          <Badge key="domain" variant="outline" className="capitalize">
            {cfg.domain_status.replace(/_/g, " ")}
          </Badge>
        );
      }
      break;
    }
    case "rds_postgres":
    case "aurora_serverless": {
      if ("backup_enabled" in cfg) {
        const enabled = cfg.backup_enabled === true;
        items.push(
          <Badge key="backup" variant="outline" className={enabled ? GREEN_BADGE : AMBER_BADGE}>
            {enabled ? "Backups enabled" : "No backup policy"}
          </Badge>
        );
      }
      if (typeof cfg.snapshot_policy === "string" && cfg.snapshot_policy) {
        items.push(
          <Badge key="snap" variant="outline" className="text-2xs font-mono">
            {cfg.snapshot_policy}
          </Badge>
        );
      }
      break;
    }
    case "s3_bucket": {
      if ("public_access_blocked" in cfg) {
        const blocked = cfg.public_access_blocked === true;
        items.push(
          <Badge key="public" variant="outline" className={blocked ? GREEN_BADGE : RED_BADGE}>
            {blocked ? "Public access blocked" : "Public access open"}
          </Badge>
        );
      }
      break;
    }
  }

  // statusError applies to every kind; surface even when the kind block
  // above produced no badges so the operator still sees the failure.
  if (service.statusError) {
    items.push(
      <TooltipProvider key="err-prov">
        <Tooltip>
          <TooltipTrigger asChild>
            <Badge variant="outline" className={`${RED_BADGE} cursor-help`}>
              Error
            </Badge>
          </TooltipTrigger>
          <TooltipContent className="max-w-sm">
            <span className="text-2xs font-mono break-words">{service.statusError}</span>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    );
  }

  if (items.length === 0) return null;
  return <>{items}</>;
}

export type ManagedServicesScreenProps = ReturnType<typeof useManagedServices> & {
  slug: string;
  /** The app detail tab row. */
  tabs?: React.ReactNode;
  /**
   * The email deliverability sheet for one service, mounted only while
   * that service is open so its queries run only then.
   */
  renderEmailDetail?: (
    service: ManagedService,
    onOpenChange: (next: boolean) => void
  ) => React.ReactNode;
  /**
   * The generic detail sheet for non-email services (ServiceDetailSheet
   * wired to its metrics query); `service` is null while it is closed.
   */
  renderServiceDetail?: (
    service: ManagedService | null,
    onOpenChange: (next: boolean) => void
  ) => React.ReactNode;
};

/** /apps/[slug]/managed-services: the service table, provision sheet and deprovision dialog. */
export function ManagedServicesScreen({
  slug,
  tabs,
  table,
  envs,
  busy,
  deprovisioning,
  onProvision,
  onDeprovision,
  renderEmailDetail,
  renderServiceDetail,
}: ManagedServicesScreenProps) {
  const [open, setOpen] = React.useState(false);
  const [deprovisionTarget, setDeprovisionTarget] = React.useState<ManagedService | null>(null);
  const [deleteData, setDeleteData] = React.useState(false);
  const [forceDestroy, setForceDestroy] = React.useState(false);
  const [emailDetailTarget, setEmailDetailTarget] = React.useState<ManagedService | null>(null);
  const [serviceDetailTarget, setServiceDetailTarget] = React.useState<ManagedService | null>(null);
  React.useEffect(() => {
    if (deprovisionTarget === null) {
      setDeleteData(false);
      setForceDestroy(false);
    }
  }, [deprovisionTarget]);

  // A deleted service is inert: no sheet, no actions. `onRowActivate` is
  // per-table rather than per-row, so the guard lives in the handler.
  const openDetail = (svc: ManagedService) => {
    if (svc.status === "deleted") return;
    if (svc.kind === "email") setEmailDetailTarget(svc);
    else setServiceDetailTarget(svc);
  };

  const columns: Column<ManagedService>[] = [
    {
      id: "health",
      header: <span className="sr-only">Status</span>,
      width: "w-8",
      cell: (svc) => <StatusDot status={STATUS_DOT[svc.status] ?? "muted"} />,
    },
    {
      id: "name",
      header: "Name",
      cellClassName: "font-mono text-xs",
      cell: (svc) => svc.name,
    },
    {
      id: "kind",
      header: "Kind",
      cell: (svc) => <Badge variant="secondary">{svc.kind}</Badge>,
    },
    {
      id: "variant",
      header: "Variant",
      cellClassName: "text-muted-foreground font-mono text-xs",
      cell: (svc) => svc.variant || "—",
    },
    {
      id: "env",
      header: "Env",
      cell: (svc) => (
        <Badge variant="outline" className="text-2xs font-mono">
          {svc.environmentName}
        </Badge>
      ),
    },
    {
      id: "status",
      header: "Status",
      cell: (svc) => (
        <div className="flex flex-wrap items-center gap-1">
          <span className="capitalize">{svc.status}</span>
          <ValidationBadges service={svc} />
        </div>
      ),
    },
    {
      id: "actions",
      header: "",
      align: "right",
      width: "w-24",
      // Above the row's stretched activator, so these stay clickable and
      // do not double-fire it.
      cellClassName: "relative z-10",
      cell: (svc) => {
        const isEmail = svc.kind === "email";
        const isActive = svc.status !== "deleted";
        return (
          <div className="flex justify-end gap-1">
            {isEmail && isActive ? (
              <Button
                variant="ghost"
                size="icon"
                className="size-8"
                onClick={() => setEmailDetailTarget(svc)}
                title="Email deliverability details"
              >
                <MailIcon className="size-4" />
                <span className="sr-only">Email details</span>
              </Button>
            ) : null}
            {!isEmail && isActive ? (
              <Button
                variant="ghost"
                size="icon"
                className="size-8"
                onClick={() => setServiceDetailTarget(svc)}
                title="Service details"
              >
                <InfoIcon className="size-4" />
                <span className="sr-only">Details</span>
              </Button>
            ) : null}
            {isActive && (
              <Can permission="app.deploy">
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-8"
                  onClick={() => setDeprovisionTarget(svc)}
                  disabled={busy}
                >
                  <Trash2Icon className="size-4" />
                  <span className="sr-only">Deprovision</span>
                </Button>
              </Can>
            )}
          </div>
        );
      },
    },
  ];

  return (
    <PageShell
      title="Managed services"
      description={
        <span className="text-muted-foreground font-mono text-xs">
          Databases, caches, queues attached to {slug}. Provisioned via the platform&apos;s driver
          registry; the workflow loop watches DB-side status and drives the upstream lifecycle.
        </span>
      }
      actions={
        <Can permission="app.deploy">
          <Button onClick={() => setOpen(true)}>
            <PlusIcon className="size-4" />
            Provision
          </Button>
        </Can>
      }
    >
      {tabs}
      <Card>
        <CardContent className="p-0">
          <DataTable
            label="Managed services"
            controller={table}
            columns={columns}
            getRowId={(svc) => svc.id}
            onRowActivate={openDetail}
            rowLabel={(svc) =>
              svc.status === "deleted" ? `${svc.name} (deleted)` : `Open ${svc.name} (${svc.kind})`
            }
            searchPlaceholder="Search by name, kind, variant, or environment…"
            empty={{
              icon: <DatabaseIcon className="size-5" />,
              title: "No managed services",
              description:
                "Provision a database, cache, queue or bucket and Astrolift wires its credentials into this app.",
            }}
            emptyFiltered={{
              title: "No matching services",
              description:
                "No service on this app matches that search. The server matches the name, kind, variant and environment.",
            }}
          />
        </CardContent>
      </Card>

      <ProvisionSheet
        open={open}
        onOpenChange={setOpen}
        envs={envs}
        onSubmit={async (input) => {
          const ok = await onProvision(input);
          if (ok) setOpen(false);
          return ok;
        }}
        busy={busy}
      />

      <AlertDialog
        open={deprovisionTarget !== null}
        onOpenChange={(next) => {
          if (!next) setDeprovisionTarget(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {deprovisionTarget
                ? `Deprovision ${deprovisionTarget.kind}/${deprovisionTarget.name}?`
                : "Deprovision managed service?"}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {deprovisionTarget
                ? `Tears down the backing resource in ${deprovisionTarget.environmentName} via the cloud's driver and soft-deletes the platform row. The workflow surfaces the in-flight state until the cloud confirms deletion.`
                : "Tears down the backing resource and soft-deletes the platform row."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="my-4 space-y-3">
            <div className="border-destructive/30 bg-destructive/5 rounded-md border p-3">
              <label className="flex cursor-pointer items-start gap-3 text-sm">
                <input
                  type="checkbox"
                  checked={deleteData}
                  onChange={(e) => setDeleteData(e.target.checked)}
                  className="border-destructive/50 accent-destructive mt-0.5 size-4 cursor-pointer rounded"
                />
                <span>
                  <strong className="text-destructive">Delete persistent data.</strong>{" "}
                  <span className="text-muted-foreground">
                    Skip the final snapshot / retained-backup path; empty buckets, purge queues,
                    drop databases. Unchecked (default), the driver keeps a restorable artifact
                    alongside the resource teardown.
                  </span>
                </span>
              </label>
            </div>
            <div className="border-destructive/30 bg-destructive/5 rounded-md border p-3">
              <label className="flex cursor-pointer items-start gap-3 text-sm">
                <input
                  type="checkbox"
                  checked={forceDestroy}
                  onChange={(e) => setForceDestroy(e.target.checked)}
                  className="border-destructive/50 accent-destructive mt-0.5 size-4 cursor-pointer rounded"
                />
                <span>
                  <strong className="text-destructive">Force destroy (--atomic).</strong>{" "}
                  <span className="text-muted-foreground">
                    Bypass cloud-side safety guards: suspend bucket versioning, ignore
                    deletion-protection flags, terminate active sessions, ignore lingering bindings.
                    Equivalent to Terraform&apos;s{" "}
                    <code className="font-mono text-xs">force_destroy = true</code>.
                  </span>
                </span>
              </label>
            </div>
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={deprovisioning}
              onClick={async (e) => {
                e.preventDefault();
                if (deprovisionTarget) {
                  const ok = await onDeprovision(deprovisionTarget, { deleteData, forceDestroy });
                  if (ok) setDeprovisionTarget(null);
                }
              }}
            >
              {deleteData && forceDestroy
                ? "Force destroy + delete data"
                : deleteData
                  ? "Deprovision + delete data"
                  : forceDestroy
                    ? "Force deprovision"
                    : "Deprovision"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {emailDetailTarget && renderEmailDetail
        ? renderEmailDetail(emailDetailTarget, (next) => {
            if (!next) setEmailDetailTarget(null);
          })
        : null}
      {renderServiceDetail?.(serviceDetailTarget, (next) => {
        if (!next) setServiceDetailTarget(null);
      })}
    </PageShell>
  );
}

export interface ProvisionSheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  envs: AstroliftAppEnvironment[];
  onSubmit: (input: ProvisionInput) => Promise<boolean>;
  busy: boolean;
}

/** Pick an environment, kind, name and variant for a new managed service. */
export function ProvisionSheet({ open, onOpenChange, envs, onSubmit, busy }: ProvisionSheetProps) {
  const [envName, setEnvName] = React.useState("");
  const [kind, setKind] = React.useState("postgres");
  const [name, setName] = React.useState("");
  const [variant, setVariant] = React.useState("");

  React.useEffect(() => {
    if (!open) {
      setEnvName(envs[0]?.name ?? "");
      setKind("postgres");
      setName("");
      setVariant("");
    }
  }, [open, envs]);

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex flex-col">
        <SheetHeader>
          <SheetTitle>Provision managed service</SheetTitle>
          <SheetDescription>
            Picks a kind + variant; the workflow loop creates the upstream resource (RDS,
            ElastiCache, etc.) and surfaces the connection envelope keys here once active.
          </SheetDescription>
        </SheetHeader>
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            if (!envName || !kind) return;
            await onSubmit({
              environmentName: envName,
              kind,
              name: name.trim() || null,
              variant: variant.trim() || null,
            });
          }}
          className="flex flex-1 flex-col gap-4 px-4 pb-4"
        >
          <div className="space-y-2">
            <Label htmlFor="ms-env">Environment</Label>
            <Select value={envName} onValueChange={setEnvName}>
              <SelectTrigger id="ms-env">
                <SelectValue placeholder="Select environment" />
              </SelectTrigger>
              <SelectContent>
                {envs.map((e) => (
                  <SelectItem key={e.id} value={e.name}>
                    {e.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="ms-kind">Kind</Label>
            <Select value={kind} onValueChange={setKind}>
              <SelectTrigger id="ms-kind">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {KIND_OPTIONS.map((k) => (
                  <SelectItem key={k} value={k}>
                    {k}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="ms-name">Name (optional)</Label>
            <Input
              id="ms-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="primary"
              spellCheck={false}
              className="font-mono"
            />
            <p className="text-muted-foreground text-xs">
              Defaults to <code>{kind}</code> when blank.
            </p>
          </div>
          <div className="space-y-2">
            <Label htmlFor="ms-variant">Variant (optional)</Label>
            <Input
              id="ms-variant"
              value={variant}
              onChange={(e) => setVariant(e.target.value)}
              placeholder="db.t4g.medium"
              spellCheck={false}
              className="font-mono"
            />
          </div>
          <SheetFooter className="mt-auto flex-row justify-end gap-2 px-0">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={busy || !envName || !kind}>
              {busy ? "Provisioning…" : "Provision"}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}
