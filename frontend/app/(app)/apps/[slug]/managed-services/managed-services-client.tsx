"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { DatabaseIcon, InfoIcon, MailIcon, PlusIcon, Trash2Icon } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { EmailDetailSheet } from "./email-detail-sheet";
import { ServiceDetailSheet } from "./service-detail-sheet";

import { Can } from "@/components/Can";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { StatusDot } from "@/components/StatusDot";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
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
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { MutationResult } from "@/graphql/identity/identity.types";
import { LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftAppEnvironment } from "@/graphql/lifecycle/lifecycle.types";
import {
  DEPROVISION_MANAGED_SERVICE,
  PROVISION_MANAGED_SERVICE,
} from "@/graphql/services/services.mutations";
import { LIST_MANAGED_SERVICES } from "@/graphql/services/services.queries";

import { AppTabs } from "../components/app-tabs";

interface ManagedService {
  id: string;
  name: string;
  kind: string;
  variant: string;
  status: string;
  statusError: string;
  config: Record<string, unknown>;
  environmentName: string;
  registeredAppSlug: string;
  createdAt: string;
  updatedAt: string;
  lastActionAt: string | null;
  lastActionKind: string;
}

interface Resp {
  astroliftManagedServices: ManagedService[];
}

interface EnvsResp {
  astroliftEnvironments: AstroliftAppEnvironment[];
}

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

const GREEN_BADGE =
  "border-emerald-500/40 bg-emerald-500/15 text-emerald-700 dark:text-emerald-300";
const AMBER_BADGE = "border-amber-500/40 bg-amber-500/15 text-amber-700 dark:text-amber-300";
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
          <Badge key="snap" variant="outline" className="font-mono text-[10px]">
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
            <span className="font-mono text-[11px] break-words">{service.statusError}</span>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    );
  }

  if (items.length === 0) return null;
  return <>{items}</>;
}

export function ManagedServicesClient({ slug }: { slug: string }) {
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
  const envs = useQuery<EnvsResp>(LIST_ENVIRONMENTS, {
    variables: { appSlug: slug },
  });
  const services = useQuery<Resp>(LIST_MANAGED_SERVICES, {
    variables: { appSlug: slug, environmentName: null },
    fetchPolicy: "cache-and-network",
    pollInterval: 15000,
  });

  const refetch = [
    {
      query: LIST_MANAGED_SERVICES,
      variables: { appSlug: slug, environmentName: null },
    },
  ];

  const [provision, provisionState] = useMutation<{
    provisionManagedService: MutationResult<ManagedService>;
  }>(PROVISION_MANAGED_SERVICE, {
    refetchQueries: refetch,
    awaitRefetchQueries: true,
  });
  const [deprovision, deprovisionState] = useMutation<{
    deprovisionManagedService: MutationResult<{ id: string; deleted: boolean }>;
  }>(DEPROVISION_MANAGED_SERVICE, {
    refetchQueries: refetch,
    awaitRefetchQueries: true,
  });

  const busy = provisionState.loading || deprovisionState.loading;
  const list = services.data?.astroliftManagedServices ?? [];
  const envList = envs.data?.astroliftEnvironments ?? [];

  async function handleDeprovision(s: ManagedService) {
    const { data } = await deprovision({
      variables: {
        input: {
          id: s.id,
          deleteData,
          forceDestroy,
        },
      },
    });
    if (data?.deprovisionManagedService.ok) {
      const verb =
        deleteData && forceDestroy
          ? "Force-deprovisioning + deleting data for"
          : deleteData
            ? "Deprovisioning + deleting data for"
            : forceDestroy
              ? "Force-deprovisioning"
              : "Deprovisioning";
      toast.success(`${verb} ${s.name}`);
      setDeprovisionTarget(null);
    } else {
      throw new Error(data?.deprovisionManagedService.errors?.[0]?.message ?? "Deprovision failed");
    }
  }

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
      <AppTabs slug={slug} active="settings" />
      <Card>
        <CardContent className="p-0">
          {services.loading && list.length === 0 ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : list.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<DatabaseIcon className="size-5" />}
                title="No managed services yet"
                description="Provision a database, cache, or queue to attach it to one of this app's environments."
              />
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead></TableHead>
                  <TableHead>Name</TableHead>
                  <TableHead>Kind</TableHead>
                  <TableHead>Variant</TableHead>
                  <TableHead>Env</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="w-12 text-right"></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {list.map((s) => {
                  // #709 — every kind except email opens the generic detail
                  // sheet on row-click. Email keeps its kind-specific sheet
                  // (deliverability + suppression + DKIM, much richer than
                  // metrics alone).
                  const isEmail = s.kind === "email";
                  const isActive = s.status !== "deleted";
                  const onRowOpen = () => {
                    if (!isActive) return;
                    if (isEmail) setEmailDetailTarget(s);
                    else setServiceDetailTarget(s);
                  };
                  return (
                    <TableRow
                      key={s.id}
                      onClick={isActive ? onRowOpen : undefined}
                      className={isActive ? "cursor-pointer" : undefined}
                    >
                      <TableCell className="w-8">
                        <StatusDot status={STATUS_DOT[s.status] ?? "muted"} />
                      </TableCell>
                      <TableCell className="font-mono text-xs">{s.name}</TableCell>
                      <TableCell>
                        <Badge variant="secondary">{s.kind}</Badge>
                      </TableCell>
                      <TableCell className="text-muted-foreground font-mono text-xs">
                        {s.variant || "—"}
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline" className="font-mono text-[10px]">
                          {s.environmentName}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        <div className="flex flex-wrap items-center gap-1">
                          <span className="capitalize">{s.status}</span>
                          <ValidationBadges service={s} />
                        </div>
                      </TableCell>
                      <TableCell
                        className="flex justify-end gap-1 text-right"
                        // Stop propagation so action-buttons in this cell
                        // don't double-trigger the row's onClick.
                        onClick={(e) => e.stopPropagation()}
                      >
                        {isEmail && isActive ? (
                          <Button
                            variant="ghost"
                            size="icon"
                            className="size-8"
                            onClick={() => setEmailDetailTarget(s)}
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
                            onClick={() => setServiceDetailTarget(s)}
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
                              onClick={() => setDeprovisionTarget(s)}
                              disabled={busy}
                            >
                              <Trash2Icon className="size-4" />
                              <span className="sr-only">Deprovision</span>
                            </Button>
                          </Can>
                        )}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <ProvisionSheet
        open={open}
        onOpenChange={setOpen}
        envs={envList}
        onSubmit={async (input) => {
          const { data } = await provision({
            variables: { input: { appSlug: slug, ...input } },
          });
          if (data?.provisionManagedService.ok) {
            toast.success(`Provisioning ${input.kind}`);
            setOpen(false);
            return true;
          }
          toast.error(data?.provisionManagedService.errors?.[0]?.message ?? "Provision failed");
          return false;
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
              disabled={deprovisionState.loading}
              onClick={async (e) => {
                e.preventDefault();
                if (deprovisionTarget) {
                  try {
                    await handleDeprovision(deprovisionTarget);
                  } catch (err) {
                    toast.error(err instanceof Error ? err.message : "Deprovision failed");
                  }
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

      {emailDetailTarget ? (
        <EmailDetailSheet
          managedServiceId={emailDetailTarget.id}
          serviceName={emailDetailTarget.name || emailDetailTarget.kind}
          appSlug={slug}
          serviceConfig={emailDetailTarget.config ?? {}}
          open={emailDetailTarget !== null}
          onOpenChange={(next) => {
            if (!next) setEmailDetailTarget(null);
          }}
        />
      ) : null}
      <ServiceDetailSheet
        service={serviceDetailTarget}
        onOpenChange={(next) => {
          if (!next) setServiceDetailTarget(null);
        }}
      />
    </PageShell>
  );
}

function ProvisionSheet({
  open,
  onOpenChange,
  envs,
  onSubmit,
  busy,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  envs: AstroliftAppEnvironment[];
  onSubmit: (input: {
    environmentName: string;
    kind: string;
    name: string | null;
    variant: string | null;
  }) => Promise<boolean>;
  busy: boolean;
}) {
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
