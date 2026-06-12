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
import { Textarea } from "@/components/ui/textarea";
import {
  LIST_CLUSTERS,
  LIST_PROVIDER_PLUGINS,
  PROVIDER_REGIONS,
  REGISTER_TENANT_CLUSTER,
} from "@/graphql/clusters/clusters.queries";
import type {
  AstroliftProviderPlugin,
  AstroliftTenantCluster,
} from "@/graphql/clusters/clusters.types";
import type { ProviderRegionsQuery } from "@/graphql/__generated__/operations";
import type { MutationResult } from "@/graphql/identity/identity.types";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const slugify = (s: string) =>
  s
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 100);

export function RegisterClusterDialog({ open, onOpenChange }: Props) {
  const plugins = useQuery<{
    astroliftProviderPlugins: AstroliftProviderPlugin[];
  }>(LIST_PROVIDER_PLUGINS);

  const [name, setName] = React.useState("");
  const [slug, setSlug] = React.useState("");
  const [slugTouched, setSlugTouched] = React.useState(false);
  const [pluginSlug, setPluginSlug] = React.useState("");
  const [region, setRegion] = React.useState("");
  const [endpoint, setEndpoint] = React.useState("");
  const [authMethod, setAuthMethod] = React.useState("kubeconfig");
  const [authConfigText, setAuthConfigText] = React.useState("{}");
  const [ingressClass, setIngressClass] = React.useState("nginx");
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    const list = plugins.data?.astroliftProviderPlugins ?? [];
    if (!pluginSlug && list.length > 0) {
      setPluginSlug(list[0].slug);
    }
  }, [plugins.data, pluginSlug]);

  React.useEffect(() => {
    if (!open) {
      setName("");
      setSlug("");
      setSlugTouched(false);
      setRegion("");
      setEndpoint("");
      setAuthConfigText("{}");
      setError(null);
    }
  }, [open]);

  const [register, { loading }] = useMutation<{
    registerTenantCluster: MutationResult<AstroliftTenantCluster>;
  }>(REGISTER_TENANT_CLUSTER, {
    refetchQueries: [{ query: LIST_CLUSTERS }],
    awaitRefetchQueries: true,
  });

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    let authConfig: unknown = {};
    try {
      authConfig = JSON.parse(authConfigText.trim() || "{}");
    } catch (err) {
      setError(err instanceof Error ? err.message : "invalid auth config JSON");
      return;
    }
    const finalSlug = slug || slugify(name);
    const { data } = await register({
      variables: {
        input: {
          name: name.trim(),
          slug: finalSlug,
          providerPluginSlug: pluginSlug,
          authMethod,
          region: region.trim() || null,
          endpoint: endpoint.trim() || null,
          ingressClass: ingressClass.trim() || "nginx",
          authConfig,
        },
      },
    });
    if (data?.registerTenantCluster.ok) {
      toast.success(`Registered ${finalSlug}`);
      onOpenChange(false);
    } else {
      toast.error(data?.registerTenantCluster.errors?.[0]?.message ?? "Failed");
    }
  }

  const pluginList = plugins.data?.astroliftProviderPlugins ?? [];

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex flex-col">
        <SheetHeader>
          <SheetTitle>Register cluster</SheetTitle>
          <SheetDescription>
            Bind a Kubernetes cluster to the platform. The control plane probes
            its capabilities on register and stores the result on the row;
            schedules and apps consult it during deploy planning.
          </SheetDescription>
        </SheetHeader>
        <form onSubmit={submit} className="flex flex-1 flex-col gap-4 overflow-y-auto px-4 pb-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="name">Display name</Label>
              <Input
                id="name"
                value={name}
                onChange={(e) => {
                  setName(e.target.value);
                  if (!slugTouched) setSlug(slugify(e.target.value));
                }}
                required
                placeholder="prd-us-west-2-tenant"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="slug">Slug</Label>
              <Input
                id="slug"
                value={slug}
                onChange={(e) => {
                  setSlug(e.target.value);
                  setSlugTouched(true);
                }}
                pattern="[a-z0-9-]+"
                required
                className="font-mono text-xs"
              />
            </div>
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="plugin">Provider plugin</Label>
              <Select value={pluginSlug} onValueChange={setPluginSlug}>
                <SelectTrigger id="plugin">
                  <SelectValue
                    placeholder={
                      pluginList.length === 0 ? "No plugins registered" : "Select plugin"
                    }
                  />
                </SelectTrigger>
                <SelectContent>
                  {pluginList.map((p) => (
                    <SelectItem key={p.slug} value={p.slug}>
                      {p.name}{" "}
                      <span className="text-muted-foreground font-mono text-xs">
                        {p.slug}
                      </span>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <RegionPicker pluginSlug={pluginSlug} value={region} onChange={setRegion} />
          </div>

          <div className="space-y-2">
            <Label htmlFor="endpoint">API endpoint</Label>
            <Input
              id="endpoint"
              value={endpoint}
              onChange={(e) => setEndpoint(e.target.value)}
              type="url"
              placeholder="https://kube.example.com"
              className="font-mono text-xs"
            />
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="auth-method">Auth method</Label>
              <Select value={authMethod} onValueChange={setAuthMethod}>
                <SelectTrigger id="auth-method">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="kubeconfig">kubeconfig</SelectItem>
                  <SelectItem value="exec_plugin">exec_plugin</SelectItem>
                  <SelectItem value="service_account_token">service_account_token</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="ingress">Ingress class</Label>
              <Input
                id="ingress"
                value={ingressClass}
                onChange={(e) => setIngressClass(e.target.value)}
                className="font-mono text-xs"
              />
            </div>
          </div>

          <div className="space-y-2">
            <Label htmlFor="auth-config">Auth config (JSON)</Label>
            <Textarea
              id="auth-config"
              value={authConfigText}
              onChange={(e) => setAuthConfigText(e.target.value)}
              rows={6}
              className="font-mono text-xs"
            />
            {error && <p className="text-destructive text-xs">{error}</p>}
            <p className="text-muted-foreground text-xs">
              Driver-specific. For kubeconfig, paste the in-cluster ServiceAccount
              token / cert bundle JSON; for exec_plugin, the exec spec.
            </p>
          </div>

          <SheetFooter className="mt-auto flex-row justify-end gap-2 px-0">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={loading || !name || !pluginSlug || pluginList.length === 0}
            >
              {loading ? "Registering…" : "Register cluster"}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}

type ProviderRegion = ProviderRegionsQuery["astroliftProviderRegions"][number];

// Region picker for the register dialog (#860). Queries the selected
// provider's regions and offers them as a searchable combobox while
// still accepting free-text entry — the typed input value *is* the
// region, and picking a suggestion just fills the slug. This keeps the
// old "type any region" escape hatch (cross-account, brand-new, or
// not-yet-in-our-table regions) working when the live driver list is
// empty or the query errors.
//
// k8s_native has no region concept, so the field is hidden entirely
// (the backend resolver returns [] for it anyway). The mutation already
// sends `region: null` when the string is empty, so a hidden field
// registers a region-less cluster cleanly.
function RegionPicker({
  pluginSlug,
  value,
  onChange,
}: {
  pluginSlug: string;
  value: string;
  onChange: (v: string) => void;
}) {
  const { data, loading } = useQuery<ProviderRegionsQuery>(PROVIDER_REGIONS, {
    variables: { providerPluginSlug: pluginSlug },
    skip: !pluginSlug || pluginSlug === "k8s_native",
    // The list rarely changes within a session; cache-first avoids a
    // refetch every time the operator re-opens the sheet.
    fetchPolicy: "cache-first",
  });

  // k8s_native: no region concept — hide the field. Mirrors the
  // provider-aware auth-gate card that hides AWS-specific controls on
  // other clouds.
  if (pluginSlug === "k8s_native") {
    return null;
  }

  const regions = data?.astroliftProviderRegions ?? [];
  const selected = regions.find((r) => r.id === value) ?? null;

  return (
    <div className="space-y-2">
      <Label htmlFor="region">Region</Label>
      <Combobox<ProviderRegion>
        items={regions}
        itemToStringLabel={(r) => r.id}
        value={selected}
        onValueChange={(v) => {
          if (v && typeof v === "object" && "id" in v) {
            onChange(v.id);
          }
        }}
        inputValue={value}
        onInputValueChange={(v) => onChange(v ?? "")}
      >
        <ComboboxInput
          id="region"
          placeholder={loading ? "Loading regions…" : "us-west-2"}
          className="font-mono text-xs"
        />
        <ComboboxContent>
          <ComboboxEmpty>
            {value
              ? `Use "${value}" (not in the list — that's fine)`
              : "No matching regions — type one in."}
          </ComboboxEmpty>
          <ComboboxList>
            {(item: ProviderRegion) => (
              <ComboboxItem key={item.id} value={item}>
                <div className="flex min-w-0 flex-1 items-center justify-between gap-2">
                  <span className="truncate font-mono text-xs">{item.id}</span>
                  <span className="text-muted-foreground truncate text-xs">{item.label}</span>
                </div>
              </ComboboxItem>
            )}
          </ComboboxList>
        </ComboboxContent>
      </Combobox>
      <p className="text-muted-foreground text-xs">
        Pick a region or type one in. Free-text is accepted for regions not in the list.
      </p>
    </div>
  );
}
