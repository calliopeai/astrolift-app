"use client";

import { useMutation } from "@apollo/client/react";
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
import { CREATE_MANAGED_DOMAIN } from "@/graphql/clusters/managed-domains.mutations";
import { LIST_MANAGED_DOMAINS } from "@/graphql/clusters/managed-domains.queries";
import type { AstroliftManagedDomain } from "@/graphql/clusters/clusters.types";
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
