"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  AlertTriangleIcon,
  BookOpenIcon,
  CheckCircle2Icon,
  CopyIcon,
  GlobeIcon,
  Loader2Icon,
  PauseIcon,
  PlayIcon,
  PlusIcon,
  RefreshCwIcon,
  ShieldCheckIcon,
  Trash2Icon,
  UploadIcon,
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
import { Textarea } from "@/components/ui/textarea";
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
  PAUSE_APP_INGRESS,
  RECHECK_DOMAIN_VALIDATION,
  REMOVE_APP_DOMAIN,
  RESUME_APP_INGRESS,
  UPLOAD_CUSTOM_DOMAIN_CERTIFICATE,
} from "@/graphql/lifecycle/lifecycle.mutations";
import { LIST_APP_DOMAINS, LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftAppEnvironment } from "@/graphql/lifecycle/lifecycle.types";
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
  // #397 cert-state surface (unhappy-path UX)
  certificateState: string;
  lastCertificateError: string;
  byoCertificateUploadedAt?: string | null;
}

interface Resp {
  astroliftAppDomains: AppDomain[];
}

interface EnvsResp {
  astroliftEnvironments: AstroliftAppEnvironment[];
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
  const envs = useQuery<EnvsResp>(LIST_ENVIRONMENTS, {
    variables,
    fetchPolicy: "cache-and-network",
  });

  const refetch = [
    { query: LIST_APP_DOMAINS, variables },
    { query: LIST_ENVIRONMENTS, variables },
  ];

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
  const [uploadCert, uploadCertState] = useMutation<{
    uploadCustomDomainCertificate: MutationResult<AppDomain>;
  }>(UPLOAD_CUSTOM_DOMAIN_CERTIFICATE, {
    refetchQueries: refetch,
    awaitRefetchQueries: true,
  });
  const [pauseIngress, pauseIngressState] = useMutation<{
    pauseAppIngress: MutationResult<AstroliftAppEnvironment>;
  }>(PAUSE_APP_INGRESS, {
    refetchQueries: refetch,
    awaitRefetchQueries: true,
  });
  const [resumeIngress, resumeIngressState] = useMutation<{
    resumeAppIngress: MutationResult<AstroliftAppEnvironment>;
  }>(RESUME_APP_INGRESS, {
    refetchQueries: refetch,
    awaitRefetchQueries: true,
  });

  const busy =
    addState.loading ||
    removeState.loading ||
    recheckState.loading ||
    uploadCertState.loading ||
    pauseIngressState.loading ||
    resumeIngressState.loading;
  const list = domains.data?.astroliftAppDomains ?? [];
  const envList = envs.data?.astroliftEnvironments ?? [];
  const [removeTarget, setRemoveTarget] = React.useState<AppDomain | null>(null);
  const [byoTarget, setByoTarget] = React.useState<AppDomain | null>(null);

  async function handleUploadCert(
    d: AppDomain,
    certificatePem: string,
    privateKeyPem: string
  ): Promise<boolean> {
    const { data } = await uploadCert({
      variables: {
        input: { id: d.id, certificatePem, privateKeyPem },
      },
    });
    if (data?.uploadCustomDomainCertificate.ok) {
      toast.success(`Certificate uploaded for ${d.hostname}`);
      return true;
    }
    toast.error(data?.uploadCustomDomainCertificate.errors?.[0]?.message ?? "Upload failed");
    return false;
  }

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
      toast.error(data?.recheckDomainValidation.errors?.[0]?.message ?? "Recheck failed");
    }
  }

  async function handleToggleIngress(env: AstroliftAppEnvironment) {
    if (env.ingressPaused) {
      const { data } = await resumeIngress({
        variables: { input: { id: env.id } },
      });
      if (data?.resumeAppIngress.ok) {
        toast.success(`Ingress resumed for ${env.name}.`);
      } else {
        toast.error(data?.resumeAppIngress.errors?.[0]?.message ?? "Resume failed.");
      }
    } else {
      const { data } = await pauseIngress({
        variables: { input: { id: env.id } },
      });
      if (data?.pauseAppIngress.ok) {
        toast.success(`Ingress paused for ${env.name}.`);
      } else {
        toast.error(data?.pauseAppIngress.errors?.[0]?.message ?? "Pause failed.");
      }
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
      <div className="space-y-4">
        {envList.length > 0 && (
          <div className="space-y-3">
            {envList.map((env) => (
              <IngressStatusCard
                key={env.id}
                env={env}
                domains={list}
                busy={busy}
                onToggle={() => handleToggleIngress(env)}
              />
            ))}
          </div>
        )}

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
                description="Add a hostname like checkout.acme.com to point at this app's primary public workload. Removing a domain later detaches it from ingress — the platform stops routing traffic for that hostname and the cert is freed."
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
                onUploadCert={() => setByoTarget(d)}
              />
            ))}
          </div>
        )}
      </div>

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

      <UploadCertSheet
        domain={byoTarget}
        open={byoTarget !== null}
        onOpenChange={(next) => {
          if (!next) setByoTarget(null);
        }}
        onSubmit={async (cert, key) => {
          if (!byoTarget) return false;
          const ok = await handleUploadCert(byoTarget, cert, key);
          if (ok) setByoTarget(null);
          return ok;
        }}
        busy={busy}
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
            The platform issues a cert via Let&apos;s Encrypt once the DNS validation record is in
            place.
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
  onUploadCert,
}: {
  domain: AppDomain;
  busy: boolean;
  onRecheck: () => void;
  onRemove: () => void;
  onUploadCert: () => void;
}) {
  const tone = CERT_TONE[domain.certState] ?? "pending";
  const label = CERT_LABEL[domain.certState] ?? domain.certState;
  const records = domain.requiredDnsRecords ?? [];
  const allPropagated = records.length > 0 && records.every((r) => r.propagated);

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
            <Button size="sm" variant="ghost" onClick={onRecheck} disabled={busy}>
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
            <span className="text-destructive font-medium">Validation error:</span>{" "}
            <span className="text-muted-foreground">{domain.lastValidationError}</span>
          </div>
        )}

        <CertStateBlock domain={domain} busy={busy} onUploadCert={onUploadCert} />

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
                        <StatusDot status={r.propagated ? "ok" : "pending"} />
                      </TableCell>
                      <TableCell className="font-mono text-xs">{r.kind}</TableCell>
                      <TableCell className="font-mono text-xs break-all">{r.name}</TableCell>
                      <TableCell className="font-mono text-xs break-all">{r.value}</TableCell>
                      <TableCell className="text-muted-foreground text-xs">{r.ttl}s</TableCell>
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
                <code className="font-mono">validated</code> on the next workflow tick (or click
                Recheck to force one).
              </p>
            )}
          </div>
        ) : (
          <p className="text-muted-foreground text-sm">
            Handshake not generated yet — re-add the domain to refresh its required records.
          </p>
        )}
      </CardContent>
    </Card>
  );
}

// ─── CertStateBlock (#397 unhappy path) ─────────────────────────────────
// Surfaces the certificate lifecycle as a separate axis from DNS
// validation. DNS can be validated while the cert is still issuing —
// rendering both axes lets the operator see exactly which step is in
// flight (and gives them an actionable BYO-cert escape hatch when
// auto-issuance fails for reasons the platform can't recover from:
// ACM-on-external-zone, LE rate limits, custom CAs, etc).

function CertStateBlock({
  domain,
  busy,
  onUploadCert,
}: {
  domain: AppDomain;
  busy: boolean;
  onUploadCert: () => void;
}) {
  const state = domain.certificateState || "not_requested";
  // not_requested with un-validated DNS: don't render — the DNS card
  // already tells the story. Once DNS validates, the worker fires
  // cert issuance and this block flips to "issuing".
  if (state === "not_requested" && domain.certState !== "validated") {
    return null;
  }
  if (state === "issuing") {
    return (
      <div className="text-muted-foreground flex items-center gap-2 rounded-md border border-amber-500/30 bg-amber-500/5 p-2 text-xs">
        <Loader2Icon className="size-3.5 animate-spin text-amber-600" />
        <span className="font-medium text-amber-700 dark:text-amber-400">
          Certificate provisioning…
        </span>
        <span>
          {domain.isPlatformManagedZone
            ? "DNS-01 challenge in progress with the cloud cert manager."
            : "HTTP-01 challenge running against your CNAME target."}
        </span>
      </div>
    );
  }
  if (state === "active") {
    return (
      <div className="flex items-center gap-2 rounded-md border border-emerald-500/30 bg-emerald-500/5 p-2 text-xs">
        <ShieldCheckIcon className="size-3.5 text-emerald-600" />
        <span className="font-medium text-emerald-700 dark:text-emerald-400">
          Certificate active
        </span>
        <span className="text-muted-foreground">
          Serving HTTPS for {domain.hostname}. The renderer picks the cert up automatically on the
          next deploy.
        </span>
      </div>
    );
  }
  if (state === "byo") {
    return (
      <div className="flex items-start gap-2 rounded-md border border-sky-500/30 bg-sky-500/5 p-2 text-xs">
        <CheckCircle2Icon className="size-3.5 shrink-0 text-sky-600" />
        <div className="flex-1 space-y-1">
          <div>
            <span className="font-medium text-sky-700 dark:text-sky-400">
              Using uploaded certificate
            </span>
            {domain.byoCertificateUploadedAt && (
              <span className="text-muted-foreground ml-2">
                Uploaded {new Date(domain.byoCertificateUploadedAt).toLocaleString()}
              </span>
            )}
          </div>
          <p className="text-muted-foreground">
            The platform won&apos;t auto-rotate this cert. Re-upload before its expiry to avoid a
            service interruption.
          </p>
        </div>
        <Can permission="app.deploy">
          <Button size="sm" variant="ghost" onClick={onUploadCert} disabled={busy}>
            <UploadIcon className="size-3.5" />
            Replace
          </Button>
        </Can>
      </div>
    );
  }
  if (state === "failed") {
    return (
      <div className="border-destructive/30 bg-destructive/5 flex items-start gap-2 rounded-md border p-2 text-xs">
        <AlertTriangleIcon className="text-destructive size-3.5 shrink-0" />
        <div className="flex-1 space-y-1">
          <div className="text-destructive font-medium">Certificate issuance failed</div>
          {domain.lastCertificateError && (
            <p className="text-muted-foreground font-mono break-all">
              {domain.lastCertificateError}
            </p>
          )}
          <p className="text-muted-foreground">
            Common causes: external DNS zone on AWS (ACM can&apos;t HTTP-01), Let&apos;s Encrypt
            rate limit on the apex, or the CNAME isn&apos;t reachable on port 80. Upload your own
            certificate to bypass auto-issuance, or fix the upstream cause and click Recheck.
          </p>
        </div>
        <Can permission="app.deploy">
          <Button size="sm" variant="default" onClick={onUploadCert} disabled={busy}>
            <UploadIcon className="size-3.5" />
            Upload cert
          </Button>
        </Can>
      </div>
    );
  }
  // not_requested but DNS validated: should be transient — show a
  // gentle hint that the worker hasn't picked it up yet.
  return (
    <div className="text-muted-foreground rounded-md border border-amber-500/30 bg-amber-500/5 p-2 text-xs">
      DNS validated. Certificate issuance queued — should start within a minute. If it doesn&apos;t,
      click Recheck to retrigger.
    </div>
  );
}

// ─── UploadCertSheet (#397 BYO path) ────────────────────────────────────
// Paste-in flow for operator-supplied PEM chain + private key. The
// backend stores the bundle; the renderer projects it as a K8s Secret
// on the runtime cluster on the next deploy.

function UploadCertSheet({
  domain,
  open,
  onOpenChange,
  onSubmit,
  busy,
}: {
  domain: AppDomain | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (certificatePem: string, privateKeyPem: string) => Promise<boolean>;
  busy: boolean;
}) {
  const [cert, setCert] = React.useState("");
  const [key, setKey] = React.useState("");

  React.useEffect(() => {
    if (!open) {
      setCert("");
      setKey("");
    }
  }, [open]);

  const certOk = cert.includes("-----BEGIN CERTIFICATE-----");
  const keyOk = key.includes("PRIVATE KEY");
  const ready = certOk && keyOk && !busy;

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex flex-col sm:max-w-2xl">
        <SheetHeader>
          <SheetTitle>
            {domain ? `Upload certificate for ${domain.hostname}` : "Upload certificate"}
          </SheetTitle>
          <SheetDescription>
            Paste the full PEM-encoded certificate chain (leaf + any intermediates) and the matching
            private key. The platform stores the bundle and projects it as a Kubernetes TLS Secret
            on the runtime cluster on the next deploy. Replaces any in-flight auto-issuance.
          </SheetDescription>
        </SheetHeader>
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            if (!ready) return;
            await onSubmit(cert.trim(), key.trim());
          }}
          className="flex flex-1 flex-col gap-4 px-4 pb-4"
        >
          <div className="space-y-2">
            <Label htmlFor="cert-pem">Certificate chain (PEM)</Label>
            <Textarea
              id="cert-pem"
              value={cert}
              onChange={(e) => setCert(e.target.value)}
              placeholder={
                "-----BEGIN CERTIFICATE-----\n...leaf...\n-----END CERTIFICATE-----\n-----BEGIN CERTIFICATE-----\n...intermediate...\n-----END CERTIFICATE-----"
              }
              spellCheck={false}
              required
              className="h-40 font-mono text-xs"
            />
            {!certOk && cert.length > 0 && (
              <p className="text-destructive text-xs">
                Doesn&apos;t look like a PEM CERTIFICATE block.
              </p>
            )}
          </div>
          <div className="space-y-2">
            <Label htmlFor="cert-key">Private key (PEM)</Label>
            <Textarea
              id="cert-key"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              placeholder={"-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----"}
              spellCheck={false}
              required
              className="h-32 font-mono text-xs"
            />
            {!keyOk && key.length > 0 && (
              <p className="text-destructive text-xs">
                Doesn&apos;t look like a PEM-encoded private key.
              </p>
            )}
          </div>
          <p className="text-muted-foreground text-xs">
            The platform won&apos;t auto-rotate this cert. Track its expiry yourself; re-upload
            before it lapses to avoid a service interruption.
          </p>
          <SheetFooter className="mt-auto flex-row justify-end gap-2 px-0">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={!ready}>
              {busy ? "Uploading…" : "Upload certificate"}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}

// ─── IngressStatusCard (#378) ───────────────────────────────────────────
// Env-scoped ingress control. Renders above the per-domain handshake
// list. Operator-facing pause / resume of all ingresses bound to the
// environment (managed hostname + custom domains alike). When paused
// the env's ingresses return HTTP 503 — used for planned maintenance
// windows where you want to freeze traffic without tearing the
// workload down. Per-domain delete still lives on the
// DomainHandshakeCard below; this card complements that with the
// env-wide toggle.

function IngressStatusCard({
  env,
  domains,
  busy,
  onToggle,
}: {
  env: AstroliftAppEnvironment;
  domains: AppDomain[];
  busy: boolean;
  onToggle: () => void;
}) {
  const paused = env.ingressPaused;
  const tone = paused ? "warn" : "ok";
  const managedHost = (() => {
    try {
      return env.url ? new URL(env.url).host : "";
    } catch {
      return env.url;
    }
  })();
  const customHosts = domains.map((d) => d.hostname);

  return (
    <Card>
      <CardContent className="space-y-3 p-5">
        <div className="flex flex-wrap items-baseline gap-3">
          <StatusDot status={tone} />
          <span className="text-sm font-semibold capitalize">{env.name}</span>
          <span className="text-muted-foreground text-xs">ingress</span>
          {paused ? (
            <Badge
              variant="outline"
              className="border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-400"
            >
              <PauseIcon className="size-3" />
              Paused
            </Badge>
          ) : (
            <Badge
              variant="outline"
              className="border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
            >
              <PlayIcon className="size-3" />
              Live
            </Badge>
          )}
          <Can permission="app.deploy">
            <Button
              size="sm"
              variant={paused ? "default" : "outline"}
              onClick={onToggle}
              disabled={busy}
              className="ml-auto"
            >
              {busy ? (
                <Loader2Icon className="size-3.5 animate-spin" />
              ) : paused ? (
                <PlayIcon className="size-3.5" />
              ) : (
                <PauseIcon className="size-3.5" />
              )}
              {paused ? "Resume ingress" : "Pause ingress"}
            </Button>
          </Can>
        </div>

        <p className="text-muted-foreground text-xs">
          When paused, the app&apos;s ingresses return HTTP 503 to all callers. Use this for planned
          maintenance windows — the workload keeps running so you can still ship deploys and observe
          metrics. Resume restores routing.
        </p>

        <div className="border-border bg-muted/30 space-y-1.5 rounded-md border p-3 text-xs">
          <div className="text-muted-foreground">Bound hostnames</div>
          {managedHost && (
            <div className="flex items-center gap-2">
              <code className="font-mono break-all">{managedHost}</code>
              <Badge variant="outline" className="text-[10px]">
                Managed
              </Badge>
            </div>
          )}
          {customHosts.length === 0
            ? !managedHost && (
                <p className="text-muted-foreground italic">No hostnames bound yet.</p>
              )
            : customHosts.map((h) => (
                <div key={h} className="flex items-center gap-2">
                  <code className="font-mono break-all">{h}</code>
                  <Badge variant="outline" className="text-[10px]">
                    Custom
                  </Badge>
                </div>
              ))}
        </div>

        {paused && (
          <div className="flex items-start gap-2 rounded-md border border-amber-500/30 bg-amber-500/5 p-2 text-xs">
            <AlertTriangleIcon className="size-3.5 shrink-0 text-amber-600" />
            <span className="text-muted-foreground">
              <span className="font-medium text-amber-700 dark:text-amber-400">
                Maintenance mode active.
              </span>{" "}
              Every bound hostname is returning 503. Health probes that hit ingress will fail;
              in-cluster probes are unaffected.
            </span>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
