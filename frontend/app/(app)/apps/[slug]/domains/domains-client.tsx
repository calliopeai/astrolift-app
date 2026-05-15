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

interface RequiredDnsRecord {
  kind: string;
  name: string;
  value: string;
  ttl: number;
  propagated: boolean;
  lastCheckedAt?: string | null;
  message: string;
}

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
  // #397 handshake surface
  txtChallengeToken: string;
  expectedCnameTarget: string;
  isPlatformManagedZone: boolean;
  lastValidationError: string;
  requiredDnsRecords: RequiredDnsRecord[];
}

interface Resp {
  astroliftAppDomains: AppDomain[];
}

const CERT_TONE: Record<string, "ok" | "warn" | "error" | "pending"> = {
  validated: "ok",
  active: "ok",
  pending: "warn",
  validating: "pending",
  failed: "error",
};

const CERT_LABEL: Record<string, string> = {
  pending: "Awaiting DNS",
  validating: "Validating…",
  validated: "Validated",
  failed: "Failed",
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
      {domains.loading && list.length === 0 ? (
        <Card>
          <CardContent className="space-y-2 p-6">
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
          </CardContent>
        </Card>
      ) : list.length === 0 ? (
        <Card>
          <CardContent className="p-6">
            <EmptyState
              icon={<GlobeIcon className="size-5" />}
              title="No custom domains yet"
              description="Add a hostname like checkout.acme.com to point at this app's primary public workload."
            />
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-4">
          {list.map((d) => (
            <DomainHandshakeCard
              key={d.id}
              domain={d}
              busy={busy}
              onRecheck={() => handleRecheck(d)}
              onRemove={() => setRemoveTarget(d)}
            />
          ))}
        </div>
      )}

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

// ─── DomainHandshakeCard (#397) ──────────────────────────────────────────
// One card per CustomDomain row. Renders the operator-facing handshake:
// the required DNS records + per-record propagation status. When the
// parent zone is platform-managed the records still render but with
// "Platform-managed" copy explaining the operator doesn't need to do
// anything.

function DomainHandshakeCard({
  domain,
  busy,
  onRecheck,
  onRemove,
}: {
  domain: AppDomain;
  busy: boolean;
  onRecheck: () => void;
  onRemove: () => void;
}) {
  const tone = CERT_TONE[domain.certState] ?? "pending";
  const label = CERT_LABEL[domain.certState] ?? domain.certState;
  const records = domain.requiredDnsRecords ?? [];
  const allPropagated =
    records.length > 0 && records.every((r) => r.propagated);

  return (
    <Card>
      <CardContent className="space-y-4 p-5">
        <div className="flex flex-wrap items-baseline gap-3">
          <StatusDot status={tone} />
          <code className="font-mono text-base">{domain.hostname}</code>
          <Badge variant="secondary">{label}</Badge>
          {domain.isPlatformManagedZone && (
            <Badge variant="outline" className="text-[10px]">
              Platform-managed zone
            </Badge>
          )}
          <span className="text-muted-foreground ml-auto text-xs">
            {domain.lastCheckedAt
              ? `Last checked ${new Date(domain.lastCheckedAt).toLocaleString()}`
              : "Not checked yet"}
          </span>
          <Can permission="app.deploy">
            <Button
              size="sm"
              variant="ghost"
              onClick={onRecheck}
              disabled={busy}
            >
              <RefreshCwIcon className="size-3.5" />
              Recheck
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="size-8"
              onClick={onRemove}
              disabled={busy}
            >
              <Trash2Icon className="size-4" />
              <span className="sr-only">Remove</span>
            </Button>
          </Can>
        </div>

        {domain.lastValidationError && (
          <div className="border-destructive/30 bg-destructive/5 rounded-md border p-2 text-xs">
            <span className="text-destructive font-medium">
              Validation error:
            </span>{" "}
            <span className="text-muted-foreground">
              {domain.lastValidationError}
            </span>
          </div>
        )}

        {records.length > 0 ? (
          <div className="space-y-2">
            <div className="text-muted-foreground text-xs">
              {domain.isPlatformManagedZone
                ? "Platform-managed records — astrolift owns the zone and creates these via the DNS driver. No operator action needed; the recheck below is a probe."
                : domain.certState === "validated"
                  ? "These records are live in your DNS. Cert active."
                  : "Add these records to your authoritative DNS, then click Recheck. The platform queries your zone's nameservers directly so propagation typically takes < 5 min."}
            </div>
            <div className="border-border rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-6"></TableHead>
                    <TableHead className="w-16">Type</TableHead>
                    <TableHead>Name</TableHead>
                    <TableHead>Value</TableHead>
                    <TableHead className="w-16">TTL</TableHead>
                    <TableHead className="w-10"></TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {records.map((r, i) => (
                    <TableRow key={`${r.kind}-${r.name}-${i}`}>
                      <TableCell>
                        <StatusDot
                          status={r.propagated ? "ok" : "pending"}
                        />
                      </TableCell>
                      <TableCell className="font-mono text-xs">
                        {r.kind}
                      </TableCell>
                      <TableCell className="font-mono text-xs break-all">
                        {r.name}
                      </TableCell>
                      <TableCell className="font-mono text-xs break-all">
                        {r.value}
                      </TableCell>
                      <TableCell className="text-muted-foreground text-xs">
                        {r.ttl}s
                      </TableCell>
                      <TableCell>
                        <button
                          onClick={() => {
                            navigator.clipboard.writeText(r.value);
                            toast.success(`${r.kind} value copied`);
                          }}
                          className="hover:bg-muted rounded p-1"
                          title="Copy value"
                        >
                          <CopyIcon className="size-3.5" />
                        </button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
            {allPropagated && domain.certState !== "validated" && (
              <p className="text-muted-foreground text-xs">
                All records propagated. Validation should flip to{" "}
                <code className="font-mono">validated</code> on the
                next workflow tick (or click Recheck to force one).
              </p>
            )}
          </div>
        ) : (
          <p className="text-muted-foreground text-sm">
            Handshake not generated yet — re-add the domain to refresh
            its required records.
          </p>
        )}
      </CardContent>
    </Card>
  );
}
