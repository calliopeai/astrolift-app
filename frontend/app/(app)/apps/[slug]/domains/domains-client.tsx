"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  BookOpenIcon,
  CopyIcon,
  GlobeIcon,
  PlusIcon,
  RefreshCwIcon,
  Trash2Icon,
} from "lucide-react";
import Link from "next/link";
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
import {
  ADD_APP_DOMAIN,
  RECHECK_DOMAIN_VALIDATION,
  REMOVE_APP_DOMAIN,
} from "@/graphql/lifecycle/lifecycle.mutations";
import { LIST_APP_DOMAINS } from "@/graphql/lifecycle/lifecycle.queries";
import type { MutationResult } from "@/graphql/identity/identity.types";
import { DOC_LINKS } from "@/lib/docs/urls";

interface AppDomain {
  id: string;
  hostname: string;
  certState: string;
  validationMethod: string;
  validationToken: string;
  lastCheckedAt?: string | null;
  isActive: boolean;
  registeredAppSlug: string;
  createdAt: string;
}

interface Resp {
  astroliftAppDomains: AppDomain[];
}

const CERT_TONE: Record<string, "ok" | "warn" | "error" | "pending"> = {
  validated: "ok",
  active: "ok",
  pending: "warn",
  failed: "error",
};

export function AppDomainsClient({ slug }: { slug: string }) {
  const [open, setOpen] = React.useState(false);
  const variables = { appSlug: slug };
  const domains = useQuery<Resp>(LIST_APP_DOMAINS, {
    variables,
    fetchPolicy: "cache-and-network",
    pollInterval: 30000,
  });

  const refetch = [{ query: LIST_APP_DOMAINS, variables }];

  const [add, addState] = useMutation<{
    addAppDomain: MutationResult<AppDomain>;
  }>(ADD_APP_DOMAIN, { refetchQueries: refetch, awaitRefetchQueries: true });
  const [remove, removeState] = useMutation<{
    removeAppDomain: MutationResult<{ id: string; deleted: boolean }>;
  }>(REMOVE_APP_DOMAIN, { refetchQueries: refetch, awaitRefetchQueries: true });
  const [recheck, recheckState] = useMutation<{
    recheckDomainValidation: MutationResult<AppDomain>;
  }>(RECHECK_DOMAIN_VALIDATION, {
    refetchQueries: refetch,
    awaitRefetchQueries: true,
  });

  const busy =
    addState.loading || removeState.loading || recheckState.loading;
  const list = domains.data?.astroliftAppDomains ?? [];
  const [removeTarget, setRemoveTarget] = React.useState<AppDomain | null>(null);

  async function handleRemove(d: AppDomain) {
    const { data } = await remove({ variables: { input: { id: d.id } } });
    if (data?.removeAppDomain.ok) {
      toast.success(`Removed ${d.hostname}`);
    } else {
      throw new Error(data?.removeAppDomain.errors?.[0]?.message ?? "Remove failed");
    }
  }

  async function handleRecheck(d: AppDomain) {
    const { data } = await recheck({ variables: { input: { id: d.id } } });
    if (data?.recheckDomainValidation.ok) {
      toast.success(`Recheck queued for ${d.hostname}`);
    } else {
      toast.error(
        data?.recheckDomainValidation.errors?.[0]?.message ?? "Recheck failed",
      );
    }
  }

  return (
    <PageShell
      title="Custom domains"
      description={`Hostnames bound to ${slug}. Add a domain, copy the validation TXT record into your DNS provider, then click Recheck once it propagates.`}
      actions={
        <>
          <Button asChild size="sm" variant="outline">
            <Link href={DOC_LINKS.customDomains}>
              <BookOpenIcon className="size-4" />
              Learn more
            </Link>
          </Button>
          <Can permission="app.deploy">
            <Button onClick={() => setOpen(true)}>
              <PlusIcon className="size-4" />
              Add domain
            </Button>
          </Can>
        </>
      }
    >
      <Card>
        <CardContent className="p-0">
          {domains.loading && list.length === 0 ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : list.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<GlobeIcon className="size-5" />}
                title="No custom domains yet"
                description="Add a hostname like checkout.acme.com to point at this app's primary public workload."
              />
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead></TableHead>
                  <TableHead>Hostname</TableHead>
                  <TableHead>Cert state</TableHead>
                  <TableHead>Validation</TableHead>
                  <TableHead>Last checked</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {list.map((d) => (
                  <TableRow key={d.id}>
                    <TableCell className="w-8">
                      <StatusDot status={CERT_TONE[d.certState] ?? "pending"} />
                    </TableCell>
                    <TableCell className="font-mono text-sm">{d.hostname}</TableCell>
                    <TableCell>
                      <Badge variant="secondary" className="capitalize">
                        {d.certState}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-xs">
                      <div className="text-muted-foreground">
                        {d.validationMethod || "—"}
                      </div>
                      {d.validationToken && (
                        <button
                          onClick={() => {
                            navigator.clipboard.writeText(d.validationToken);
                            toast.success("Token copied");
                          }}
                          className="mt-1 inline-flex items-center gap-1 font-mono text-[11px] hover:underline"
                        >
                          <CopyIcon className="size-3" />
                          {d.validationToken.slice(0, 32)}…
                        </button>
                      )}
                    </TableCell>
                    <TableCell className="text-muted-foreground text-xs">
                      {d.lastCheckedAt
                        ? new Date(d.lastCheckedAt).toLocaleString()
                        : "—"}
                    </TableCell>
                    <TableCell className="text-right">
                      <Can permission="app.deploy">
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => handleRecheck(d)}
                          disabled={busy}
                        >
                          <RefreshCwIcon className="size-3.5" />
                          Recheck
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="size-8"
                          onClick={() => setRemoveTarget(d)}
                          disabled={busy}
                        >
                          <Trash2Icon className="size-4" />
                          <span className="sr-only">Remove</span>
                        </Button>
                      </Can>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <AddDomainSheet
        open={open}
        onOpenChange={setOpen}
        onSubmit={async (hostname, validationMethod) => {
          const { data } = await add({
            variables: {
              input: {
                appSlug: slug,
                hostname,
                validationMethod: validationMethod || null,
              },
            },
          });
          if (data?.addAppDomain.ok) {
            toast.success(`Added ${hostname}`);
            setOpen(false);
            return true;
          }
          toast.error(data?.addAppDomain.errors?.[0]?.message ?? "Add failed");
          return false;
        }}
        busy={busy}
      />

      <ConfirmDialog
        open={removeTarget !== null}
        onOpenChange={(next) => {
          if (!next) setRemoveTarget(null);
        }}
        title={removeTarget ? `Remove ${removeTarget.hostname}?` : "Remove domain?"}
        description="Soft-deletes the app↔hostname binding. The cert is freed and the hostname becomes available to re-add to another app. The DNS record at your provider is unaffected."
        confirmLabel="Remove domain"
        destructive
        onConfirm={async () => {
          if (removeTarget) await handleRemove(removeTarget);
        }}
      />
    </PageShell>
  );
}

function AddDomainSheet({
  open,
  onOpenChange,
  onSubmit,
  busy,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (hostname: string, validationMethod: string) => Promise<boolean>;
  busy: boolean;
}) {
  const [hostname, setHostname] = React.useState("");
  const [validationMethod, setValidationMethod] = React.useState("");

  React.useEffect(() => {
    if (!open) {
      setHostname("");
      setValidationMethod("");
    }
  }, [open]);

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex flex-col">
        <SheetHeader>
          <SheetTitle>Add custom domain</SheetTitle>
          <SheetDescription>
            The platform issues a cert via Let&apos;s Encrypt once the
            DNS validation record is in place.
          </SheetDescription>
        </SheetHeader>
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            if (!hostname.trim()) return;
            await onSubmit(hostname.trim().toLowerCase(), validationMethod.trim());
          }}
          className="flex flex-1 flex-col gap-4 px-4 pb-4"
        >
          <div className="space-y-2">
            <Label htmlFor="d-hostname">Hostname</Label>
            <Input
              id="d-hostname"
              value={hostname}
              onChange={(e) => setHostname(e.target.value)}
              placeholder="checkout.acme.com"
              autoFocus
              required
              spellCheck={false}
              className="font-mono"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="d-method">Validation method (optional)</Label>
            <Input
              id="d-method"
              value={validationMethod}
              onChange={(e) => setValidationMethod(e.target.value)}
              placeholder="dns-01"
              spellCheck={false}
              className="font-mono"
            />
            <p className="text-muted-foreground text-xs">
              Defaults to the platform&apos;s preferred method when blank.
            </p>
          </div>
          <SheetFooter className="mt-auto flex-row justify-end gap-2 px-0">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={busy || !hostname.trim()}>
              {busy ? "Adding…" : "Add domain"}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}
