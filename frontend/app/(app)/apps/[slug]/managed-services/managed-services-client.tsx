"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { DatabaseIcon, PlusIcon, Trash2Icon } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { StatusDot } from "@/components/StatusDot";
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

interface ManagedService {
  id: string;
  name: string;
  kind: string;
  variant: string;
  status: string;
  config: Record<string, unknown>;
  environmentName: string;
  registeredAppSlug: string;
  createdAt: string;
  updatedAt: string;
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

export function ManagedServicesClient({ slug }: { slug: string }) {
  const [open, setOpen] = React.useState(false);
  const [deprovisionTarget, setDeprovisionTarget] = React.useState<ManagedService | null>(null);
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
    const { data } = await deprovision({ variables: { input: { id: s.id } } });
    if (data?.deprovisionManagedService.ok) {
      toast.success(`Deprovisioning ${s.name}`);
    } else {
      throw new Error(
        data?.deprovisionManagedService.errors?.[0]?.message ?? "Deprovision failed",
      );
    }
  }

  return (
    <PageShell
      title="Managed services"
      description={`Databases, caches, queues attached to ${slug}. Provisioned via the platform's driver registry; the workflow loop watches DB-side status and drives the upstream lifecycle.`}
      actions={
        <Can permission="app.deploy">
          <Button onClick={() => setOpen(true)}>
            <PlusIcon className="size-4" />
            Provision
          </Button>
        </Can>
      }
    >
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
                {list.map((s) => (
                  <TableRow key={s.id}>
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
                    <TableCell className="capitalize">{s.status}</TableCell>
                    <TableCell className="text-right">
                      {s.status !== "deleted" && (
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
                ))}
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
          toast.error(
            data?.provisionManagedService.errors?.[0]?.message ?? "Provision failed",
          );
          return false;
        }}
        busy={busy}
      />

      <ConfirmDialog
        open={deprovisionTarget !== null}
        onOpenChange={(next) => {
          if (!next) setDeprovisionTarget(null);
        }}
        title={
          deprovisionTarget
            ? `Deprovision ${deprovisionTarget.kind}/${deprovisionTarget.name}?`
            : "Deprovision managed service?"
        }
        description={
          deprovisionTarget
            ? `Soft-deletes the row in ${deprovisionTarget.environmentName} and signals the workflow loop to tear down upstream resources. Irreversible once upstream teardown completes.`
            : "Soft-deletes the row and signals the workflow loop to tear down upstream resources."
        }
        confirmLabel="Deprovision"
        destructive
        onConfirm={async () => {
          if (deprovisionTarget) await handleDeprovision(deprovisionTarget);
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
            Picks a kind + variant; the workflow loop creates the upstream
            resource (RDS, ElastiCache, etc.) and surfaces the connection
            envelope keys here once active.
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
