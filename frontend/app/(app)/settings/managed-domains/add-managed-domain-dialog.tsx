"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Combobox,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxInput,
  ComboboxItem,
  ComboboxList,
} from "@/components/ui/combobox";
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
import { CREATE_MANAGED_DOMAIN } from "@/graphql/clusters/managed-domains.mutations";
import {
  DNS_CERTIFICATES,
  DNS_ZONES,
} from "@/graphql/clusters/clusters.queries";
import { LIST_MANAGED_DOMAINS } from "@/graphql/clusters/managed-domains.queries";
import type { AstroliftManagedDomain } from "@/graphql/clusters/clusters.types";
import type {
  DnsCertificatesQuery,
  DnsZonesQuery,
} from "@/graphql/__generated__/operations";
import type { MutationResult } from "@/graphql/identity/identity.types";

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

const DNS_CONFIG_META: Record<DnsDriver, { placeholder: string; hint: string }> = {
  route53: {
    placeholder: '{"zone_id": "Z1234567890ABCDEF", "certificate_arn": "arn:aws:acm:us-east-1:123456789012:certificate/..."}',
    hint: "Route 53 hosted zone ID and optional ACM certificate ARN. Empty is fine — the driver will discover what it can.",
  },
  cloud_dns: {
    placeholder: '{"project": "my-gcp-project", "managed_zone": "my-zone-name"}',
    hint: "GCP project ID and Cloud DNS managed zone name. Empty is fine — the driver will discover what it can.",
  },
  azure_dns: {
    placeholder: '{"resource_group": "my-resource-group", "zone_name": "example.com"}',
    hint: "Azure resource group and DNS zone name. Empty is fine — the driver will discover what it can.",
  },
};

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
    }
  }, [open]);

  const [createDomain, { loading }] = useMutation<{
    createManagedDomain: MutationResult<AstroliftManagedDomain>;
  }>(CREATE_MANAGED_DOMAIN, {
    refetchQueries: [{ query: LIST_MANAGED_DOMAINS }],
    awaitRefetchQueries: true,
  });

  // #861 — hosted-zone discovery, keyed by the selected DNS driver.
  // Re-fetches automatically when the driver dropdown changes (the
  // variables change). `supported=false` for drivers without zone
  // discovery wired (cloud_dns / azure_dns today) → the picker is
  // hidden and the textarea stays the manual fallback.
  const zonesQuery = useQuery<DnsZonesQuery>(DNS_ZONES, {
    variables: { dnsDriver },
    skip: !open,
    fetchPolicy: "cache-and-network",
  });
  const zonesPayload = zonesQuery.data?.astroliftDnsZones;
  const zones = zonesPayload?.zones ?? [];
  const zonesSupported = zonesPayload?.supported ?? false;

  // #858 — cert discovery for the certificate_arn key. Only route53
  // surfaces certs today (ACM via ambient creds); selecting one merges
  // it into the dnsConfig JSON without disturbing the operator's other
  // keys.
  const certsQuery = useQuery<DnsCertificatesQuery>(DNS_CERTIFICATES, {
    variables: { dnsDriver },
    skip: !open,
    fetchPolicy: "cache-and-network",
  });
  const certsPayload = certsQuery.data?.astroliftDnsCertificates;
  const certs = certsPayload?.certificates ?? [];
  const certsSupported = certsPayload?.supported ?? false;

  // Merge a chosen certificate ARN into the current DNS-config JSON.
  // Parses what the operator currently has (defaulting to an empty
  // object), sets certificate_arn, and re-serializes — so picking a
  // cert never clobbers a hand-typed zone_id or other keys. If the
  // current text isn't valid JSON we surface the parse error rather
  // than silently overwriting their edits.
  function applyCertArn(arn: string) {
    setJsonError(null);
    let base: Record<string, unknown> = {};
    const trimmed = dnsConfig.trim();
    if (trimmed.length > 0) {
      try {
        const parsed = JSON.parse(trimmed);
        if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
          base = parsed as Record<string, unknown>;
        }
      } catch {
        setJsonError("Fix the DNS config JSON before applying a certificate.");
        return;
      }
    }
    base.certificate_arn = arn;
    setDnsConfig(JSON.stringify(base, null, 2));
  }

  function certLabel(c: { name: string; domainName: string; status: string }): string {
    const label = c.domainName || c.name;
    return c.status && c.status !== "ISSUED" ? `${label} · ${c.status}` : label;
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setJsonError(null);
    setSubmitError(null);

    const trimmedConfig = dnsConfig.trim();
    let parsedConfig: unknown = null;
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

          {/* #861 — hosted-zone picker. Selecting a zone auto-fills the
              config textarea below from the zone's pre-serialized
              configJson; the operator can still edit it afterward.
              Shown only when the driver supports zone discovery. */}
          {zonesSupported && (
            <div className="space-y-2">
              <Label htmlFor="dns-zone">Hosted zone</Label>
              <Combobox
                items={zones}
                itemToStringLabel={(z) => (z as { name: string }).name}
                onValueChange={(v) => {
                  if (v && typeof v === "object" && "configJson" in v) {
                    setJsonError(null);
                    setDnsConfig((v as { configJson: string }).configJson);
                  }
                }}
              >
                <ComboboxInput
                  id="dns-zone"
                  placeholder={
                    zonesQuery.loading && zones.length === 0
                      ? "Loading zones…"
                      : `Search ${zones.length} zone${zones.length === 1 ? "" : "s"}…`
                  }
                />
                <ComboboxContent>
                  <ComboboxEmpty>No matching zones.</ComboboxEmpty>
                  <ComboboxList>
                    {(item) => {
                      const z = item as {
                        id: string;
                        name: string;
                        private: boolean;
                      };
                      return (
                        <ComboboxItem key={z.id} value={z}>
                          <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                            <span className="truncate font-mono text-xs">{z.name}</span>
                            <span className="text-muted-foreground text-[10px]">
                              {z.id}
                              {z.private ? " · private" : ""}
                            </span>
                          </div>
                        </ComboboxItem>
                      );
                    }}
                  </ComboboxList>
                </ComboboxContent>
              </Combobox>
              <p className="text-muted-foreground text-[11px]">
                Pick a discovered zone to pre-fill the config below, or skip
                and enter it by hand.
              </p>
            </div>
          )}

          {/* #858 — certificate picker. Fills the certificate_arn key in
              the config JSON below without disturbing other keys. Shown
              only when the driver can list certs (route53 → ACM). */}
          {certsSupported && certs.length > 0 && (
            <div className="space-y-2">
              <Label htmlFor="dns-cert">Certificate (optional)</Label>
              <Combobox
                items={certs}
                itemToStringLabel={(c) =>
                  certLabel(c as { name: string; domainName: string; status: string })
                }
                onValueChange={(v) => {
                  if (v && typeof v === "object" && "arn" in v) {
                    applyCertArn((v as { arn: string }).arn);
                  }
                }}
              >
                <ComboboxInput
                  id="dns-cert"
                  placeholder={`Search ${certs.length} certificate${certs.length === 1 ? "" : "s"}…`}
                />
                <ComboboxContent>
                  <ComboboxEmpty>No matching certificates.</ComboboxEmpty>
                  <ComboboxList>
                    {(item) => {
                      const c = item as {
                        arn: string;
                        name: string;
                        domainName: string;
                        status: string;
                      };
                      return (
                        <ComboboxItem key={c.arn} value={c}>
                          <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                            <span className="truncate text-xs">{c.domainName || c.name}</span>
                            <span className="text-muted-foreground truncate font-mono text-[10px]">
                              {c.arn}
                            </span>
                          </div>
                        </ComboboxItem>
                      );
                    }}
                  </ComboboxList>
                </ComboboxContent>
              </Combobox>
              <p className="text-muted-foreground text-[11px]">
                Sets <code className="font-mono">certificate_arn</code> in the config below.
              </p>
            </div>
          )}

          <div className="space-y-2">
            <Label htmlFor="dns-config">DNS config (JSON, optional)</Label>
            <Textarea
              id="dns-config"
              value={dnsConfig}
              onChange={(e) => setDnsConfig(e.target.value)}
              rows={4}
              placeholder={DNS_CONFIG_META[dnsDriver].placeholder}
              className="font-mono text-xs"
            />
            {jsonError && <p className="text-destructive text-xs">{jsonError}</p>}
            <p className="text-muted-foreground text-[11px]">
              {DNS_CONFIG_META[dnsDriver].hint}
            </p>
          </div>

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
