"use client";

import { useLazyQuery, useMutation } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { PROVIDER_CERTS, PROVIDER_HOSTED_ZONES } from "@/graphql/clusters/clusters.queries";
import { CREATE_MANAGED_DOMAIN } from "@/graphql/clusters/managed-domains.mutations";
import { LIST_MANAGED_DOMAINS } from "@/graphql/clusters/managed-domains.queries";
import type { AstroliftManagedDomain } from "@/graphql/clusters/clusters.types";
import type { MutationResult } from "@/graphql/identity/identity.types";

interface ProviderCert {
  arn: string;
  domain: string;
  status: string;
  notAfter: string;
}

interface ProviderHostedZone {
  zoneId: string;
  zoneName: string;
  recordCount: number;
}

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

type DnsDriver = "route53" | "cloud_dns" | "azure_dns";
type DefaultFor = "tenant_apps" | "preview_envs" | "both" | "none";

const DRIVER_OPTIONS: { value: DnsDriver; label: string }[] = [
  { value: "route53", label: "AWS Route 53" },
  { value: "cloud_dns", label: "Google Cloud DNS" },
  { value: "azure_dns", label: "Azure DNS" },
];

const DEFAULT_FOR_OPTIONS: { value: DefaultFor; label: string }[] = [
  { value: "both", label: "Both" },
  { value: "tenant_apps", label: "Tenant apps" },
  { value: "preview_envs", label: "Preview envs" },
  { value: "none", label: "None" },
];

export function AddManagedDomainDialog({ open, onOpenChange }: Props) {
  const [zone, setZone] = React.useState("");
  const [dnsDriver, setDnsDriver] = React.useState<DnsDriver>("route53");
  const [defaultFor, setDefaultFor] = React.useState<DefaultFor>("both");
  const [wildcard, setWildcard] = React.useState(true);
  const [dnsConfig, setDnsConfig] = React.useState("");
  const [orgScoped, setOrgScoped] = React.useState(false);
  const [jsonError, setJsonError] = React.useState<string | null>(null);
  const [submitError, setSubmitError] = React.useState<string | null>(null);

  // Picker state for route53 driver (#858, #861).
  const [pickedZoneId, setPickedZoneId] = React.useState("");
  const [pickedCertArn, setPickedCertArn] = React.useState("");

  const [fetchHostedZones, hostedZonesResult] = useLazyQuery<{
    astroliftProviderHostedZones: ProviderHostedZone[];
  }>(PROVIDER_HOSTED_ZONES);

  const [fetchCerts, certsResult] = useLazyQuery<{
    astroliftProviderCerts: ProviderCert[];
  }>(PROVIDER_CERTS);

  // When driver flips to route53 and the dialog is open, fetch picker data.
  React.useEffect(() => {
    if (!open) return;
    if (dnsDriver === "route53") {
      fetchHostedZones({ variables: { pluginSlug: "aws" } });
      fetchCerts({ variables: { pluginSlug: "aws" } });
    }
  }, [open, dnsDriver, fetchHostedZones, fetchCerts]);

  const hostedZones = hostedZonesResult.data?.astroliftProviderHostedZones ?? [];
  const certs = certsResult.data?.astroliftProviderCerts ?? [];
  // Only show ISSUED certs in the picker; others go in the fallback textarea.
  const issuedCerts = certs.filter((c) => c.status === "ISSUED");

  React.useEffect(() => {
    if (!open) {
      setZone("");
      setDnsDriver("route53");
      setDefaultFor("both");
      setWildcard(true);
      setDnsConfig("");
      setOrgScoped(false);
      setJsonError(null);
      setSubmitError(null);
      setPickedZoneId("");
      setPickedCertArn("");
    }
  }, [open]);

  const [createDomain, { loading }] = useMutation<{
    createManagedDomain: MutationResult<AstroliftManagedDomain>;
  }>(CREATE_MANAGED_DOMAIN, {
    refetchQueries: [{ query: LIST_MANAGED_DOMAINS }],
    awaitRefetchQueries: true,
  });

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setJsonError(null);
    setSubmitError(null);

    // For route53, merge picker selections into the config object.
    let parsedConfig: unknown = null;
    if (dnsDriver === "route53" && (pickedZoneId || pickedCertArn)) {
      const pickerConfig: Record<string, string> = {};
      if (pickedZoneId) pickerConfig.zone_id = pickedZoneId;
      if (pickedCertArn) pickerConfig.certificate_arn = pickedCertArn;
      // Merge with any manual JSON the operator also typed.
      const trimmedManual = dnsConfig.trim();
      if (trimmedManual.length > 0) {
        try {
          const manualParsed = JSON.parse(trimmedManual) as Record<string, unknown>;
          parsedConfig = { ...manualParsed, ...pickerConfig };
        } catch (err) {
          setJsonError(err instanceof Error ? err.message : "DNS config is not valid JSON");
          return;
        }
      } else {
        parsedConfig = pickerConfig;
      }
    } else {
      const trimmedConfig = dnsConfig.trim();
      if (trimmedConfig.length > 0) {
        try {
          parsedConfig = JSON.parse(trimmedConfig);
        } catch (err) {
          setJsonError(
            err instanceof Error ? err.message : "DNS config is not valid JSON"
          );
          return;
        }
      }
    }

    try {
      const { data } = await createDomain({
        variables: {
          input: {
            zone: zone.trim(),
            dnsDriver,
            defaultFor,
            isWildcardManaged: wildcard,
            organizationScoped: orgScoped,
            dnsConfig: parsedConfig,
          },
        },
      });

      if (data?.createManagedDomain.ok) {
        toast.success(`Added ${data.createManagedDomain.data?.zone ?? zone}`);
        onOpenChange(false);
      } else {
        setSubmitError(
          data?.createManagedDomain.errors?.[0]?.message ?? "Failed to add domain"
        );
      }
    } catch (err) {
      setSubmitError(
        err instanceof Error ? err.message : "Unexpected error — check console"
      );
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add managed domain</DialogTitle>
          <DialogDescription>
            Bind a DNS zone to a driver. Tenant apps and previews under this
            zone get records created and updated automatically by the driver.
          </DialogDescription>
        </DialogHeader>

        <form id="add-managed-domain" onSubmit={submit} className="flex flex-col gap-4">
          <div className="space-y-2">
            <Label htmlFor="zone">Zone</Label>
            <Input
              id="zone"
              value={zone}
              onChange={(e) => setZone(e.target.value)}
              placeholder="astrolift.example.com"
              required
              autoFocus
              className="font-mono text-xs"
            />
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="dns-driver">DNS driver</Label>
              <Select
                value={dnsDriver}
                onValueChange={(v) => setDnsDriver(v as DnsDriver)}
              >
                <SelectTrigger id="dns-driver">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {DRIVER_OPTIONS.map((opt) => (
                    <SelectItem key={opt.value} value={opt.value}>
                      {opt.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-2">
              <Label htmlFor="default-for">Default for</Label>
              <Select
                value={defaultFor}
                onValueChange={(v) => setDefaultFor(v as DefaultFor)}
              >
                <SelectTrigger id="default-for">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {DEFAULT_FOR_OPTIONS.map((opt) => (
                    <SelectItem key={opt.value} value={opt.value}>
                      {opt.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          <label className="flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              checked={wildcard}
              onChange={(e) => setWildcard(e.target.checked)}
              className="mt-0.5"
            />
            <span>
              <span className="font-medium">Wildcard cert managed by platform</span>
              <span className="text-muted-foreground mt-0.5 block text-xs">
                Issue and renew *.{zone || "<zone>"} automatically.
              </span>
            </span>
          </label>

          <label className="flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              checked={orgScoped}
              onChange={(e) => setOrgScoped(e.target.checked)}
              className="mt-0.5"
            />
            <span>
              <span className="font-medium">Scoped to current organization</span>
              <span className="text-muted-foreground mt-0.5 block text-xs">
                Leave off for a platform-wide zone shared across orgs.
              </span>
            </span>
          </label>

          {dnsDriver === "route53" ? (
            <div className="space-y-3">
              <div className="space-y-2">
                <Label htmlFor="hosted-zone">Hosted zone</Label>
                {hostedZones.length > 0 ? (
                  <Select value={pickedZoneId} onValueChange={(v) => {
                    setPickedZoneId(v);
                    // Auto-fill the zone name field when the operator picks a zone.
                    const picked = hostedZones.find((z) => z.zoneId === v);
                    if (picked && !zone) setZone(picked.zoneName);
                  }}>
                    <SelectTrigger id="hosted-zone">
                      <SelectValue placeholder="Select hosted zone" />
                    </SelectTrigger>
                    <SelectContent>
                      {hostedZones.map((z) => (
                        <SelectItem key={z.zoneId} value={z.zoneId}>
                          <span className="font-mono">{z.zoneName}</span>
                          <span className="text-muted-foreground ml-2 text-xs">{z.zoneId}</span>
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                ) : (
                  <p className="text-muted-foreground text-xs">
                    No hosted zones found — enter the zone ID in the config below.
                  </p>
                )}
              </div>

              <div className="space-y-2">
                <Label htmlFor="cert-arn">ACM certificate (optional)</Label>
                {issuedCerts.length > 0 ? (
                  <Select value={pickedCertArn} onValueChange={setPickedCertArn}>
                    <SelectTrigger id="cert-arn">
                      <SelectValue placeholder="Select certificate" />
                    </SelectTrigger>
                    <SelectContent>
                      {issuedCerts.map((c) => (
                        <SelectItem key={c.arn} value={c.arn}>
                          <span className="font-mono">{c.domain}</span>
                          <span className="text-muted-foreground ml-2 text-xs">
                            {c.notAfter ? `expires ${c.notAfter.slice(0, 10)}` : ""}
                          </span>
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                ) : (
                  <p className="text-muted-foreground text-xs">
                    No ISSUED certificates found — leave blank or enter a cert ARN in the config below.
                  </p>
                )}
              </div>

              <div className="space-y-2">
                <Label htmlFor="dns-config">Additional config (JSON, optional)</Label>
                <Textarea
                  id="dns-config"
                  value={dnsConfig}
                  onChange={(e) => setDnsConfig(e.target.value)}
                  rows={3}
                  placeholder='{"zone_id": "...", "certificate_arn": "..."}'
                  className="font-mono text-xs"
                />
                {jsonError && <p className="text-destructive text-xs">{jsonError}</p>}
                <p className="text-muted-foreground text-[11px]">
                  Override or add extra keys. Picker selections above take precedence over the same keys here.
                </p>
              </div>
            </div>
          ) : (
            <div className="space-y-2">
              <Label htmlFor="dns-config">DNS config (JSON, optional)</Label>
              <Textarea
                id="dns-config"
                value={dnsConfig}
                onChange={(e) => setDnsConfig(e.target.value)}
                rows={4}
                placeholder={'{"zone_id": "...", "certificate_arn": "..."}'}
                className="font-mono text-xs"
              />
              {jsonError && <p className="text-destructive text-xs">{jsonError}</p>}
              <p className="text-muted-foreground text-[11px]">
                Driver-specific identifiers (hosted zone ID, ACM cert ARN, GCP
                project, etc.). Empty is fine — the driver will discover what
                it can.
              </p>
            </div>
          )}

          {submitError && (
            <p className="text-destructive text-sm">{submitError}</p>
          )}
        </form>

        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={() => onOpenChange(false)}
          >
            Cancel
          </Button>
          <Button
            type="submit"
            form="add-managed-domain"
            disabled={loading || !zone.trim()}
          >
            {loading ? "Adding…" : "Add domain"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
