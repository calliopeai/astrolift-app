"use client";

import { AlertTriangleIcon, ArrowLeftIcon, ArrowRightIcon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";

import { ShellHeader } from "@/components/shell/ShellHeader";
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
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

import { localizedClusterCrumbs } from "./clusters-list";
import type {
  ProviderRegion,
  RegisterField,
  RegisterResult,
  useRegisterCluster,
} from "./use-register-cluster";

export type RegisterClusterPageProps = ReturnType<typeof useRegisterCluster> & {
  /** Where Cancel goes: the Clusters list. */
  cancelHref: string;
  /** Start on a step; stories use it. */
  initialStep?: 1 | 2;
  /** Errors to open with; stories use it. */
  initialErrors?: Partial<Record<RegisterField, string>>;
};

const slugify = (s: string) =>
  s
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 100);

const SLUG = /^[a-z0-9-]+$/;

// Expected auth_config shape per method — the exact keys the platform reads in
// providers/k8s_native/observability.py::build_api_client (and _config_for for
// exec_plugin). Auto-seeded into the field so operators see the right shape
// instead of pasting a structurally-valid-but-wrong blob (#902).
function authConfigExample(authMethod: string, t: ReturnType<typeof useTranslations>): string {
  switch (authMethod) {
    case "kubeconfig":
      return JSON.stringify({ kubeconfig: `<${t("exampleKubeconfig")}>` }, null, 2);
    case "service_account_token":
      return JSON.stringify(
        { token: `<${t("exampleToken")}>`, ca_cert: `<${t("exampleCa")}>` },
        null,
        2
      );
    case "exec_plugin":
      return JSON.stringify(
        { region: `<${t("exampleRegion")}>`, cluster_name: `<${t("exampleCluster")}>` },
        null,
        2
      );
    default:
      return "{}";
  }
}

function authConfigHint(authMethod: string, t: ReturnType<typeof useTranslations>): string {
  switch (authMethod) {
    case "kubeconfig":
      return t("hintKubeconfig");
    case "service_account_token":
      return t("hintServiceAccount");
    case "exec_plugin":
      return t("hintExecPlugin");
    default:
      return t("hintOther");
  }
}

const STEPS = [
  { n: 1, key: "cluster" },
  { n: 2, key: "connection" },
] as const;

/** Which step holds a field, so a refused register opens where its error is. */
const STEP_OF: Record<RegisterField, 1 | 2> = {
  name: 1,
  slug: 1,
  providerPluginSlug: 1,
  region: 1,
  endpoint: 2,
  authMethod: 2,
  ingressClass: 2,
  authConfig: 2,
};

type Errors = Partial<Record<RegisterField, string>>;

/** A label, the control, its error in place, then its help text. */
function Field({
  id,
  label,
  error,
  help,
  className,
  children,
}: {
  id: string;
  label: string;
  error?: string;
  help?: React.ReactNode;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div className={cn("min-w-0 space-y-2", className)}>
      <Label htmlFor={id}>{label}</Label>
      {children}
      {error && (
        <p
          id={`${id}-error`}
          role="alert"
          className="text-destructive text-xs [overflow-wrap:anywhere]"
        >
          {error}
        </p>
      )}
      {help && <p className="text-muted-foreground text-xs">{help}</p>}
    </div>
  );
}

function invalid(id: string, error?: string) {
  return error ? { "aria-invalid": true, "aria-describedby": `${id}-error` } : {};
}

/**
 * Admin › Clusters › Register cluster: a page in two steps, since it asks for
 * eight fields (spec 44 §5.4). Errors stand beside their fields; the outcome
 * is the hook's toast. Pure view; the data half is useRegisterCluster.
 */
export function RegisterClusterPage({
  plugins,
  pluginSlug,
  onPluginSlugChange,
  regions,
  regionsLoading,
  registering,
  onRegister,
  cancelHref,
  initialStep = 1,
  initialErrors = {},
}: RegisterClusterPageProps) {
  const t = useTranslations("clusters.registration");
  const chrome = useTranslations("clusters.chrome");
  const [step, setStep] = React.useState<1 | 2>(initialStep);
  const [name, setName] = React.useState("");
  const [slug, setSlug] = React.useState("");
  const [slugTouched, setSlugTouched] = React.useState(false);
  const [region, setRegion] = React.useState("");
  const [endpoint, setEndpoint] = React.useState("");
  const [authMethod, setAuthMethod] = React.useState("kubeconfig");
  const [authConfigText, setAuthConfigText] = React.useState(() =>
    authConfigExample("kubeconfig", t)
  );
  // Once the operator edits the JSON we stop auto-swapping the example on
  // method change, so we never clobber real input.
  const [authConfigTouched, setAuthConfigTouched] = React.useState(false);
  const [rawKubeconfig, setRawKubeconfig] = React.useState("");
  const [ingressClass, setIngressClass] = React.useState("nginx");
  const [errors, setErrors] = React.useState<Errors>(initialErrors);
  const [formError, setFormError] = React.useState<string | null>(null);

  const clear = (field: RegisterField) =>
    setErrors((e) => {
      if (!e[field]) return e;
      const next = { ...e };
      delete next[field];
      return next;
    });

  function handleAuthMethodChange(next: string) {
    setAuthMethod(next);
    clear("authMethod");
    if (!authConfigTouched) setAuthConfigText(authConfigExample(next, t));
  }

  // Wrap a raw kubeconfig (multi-line YAML) into the escaped JSON envelope —
  // JSON.stringify handles the newline/quote escaping operators would
  // otherwise do by hand.
  function applyRawKubeconfig() {
    const raw = rawKubeconfig.trim();
    if (!raw) return;
    setAuthConfigText(JSON.stringify({ kubeconfig: raw }, null, 2));
    setAuthConfigTouched(true);
    setRawKubeconfig("");
    clear("authConfig");
  }

  function validateCluster(): Errors {
    const e: Errors = {};
    if (!name.trim()) e.name = t("nameRequired");
    const s = slug || slugify(name);
    if (!s) e.slug = t("slugRequired");
    else if (!SLUG.test(s)) e.slug = t("slugInvalid");
    if (!pluginSlug) e.providerPluginSlug = t("pluginRequired");
    return e;
  }

  function next(e: React.FormEvent) {
    e.preventDefault();
    const found = validateCluster();
    setErrors(found);
    if (Object.keys(found).length === 0) setStep(2);
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setFormError(null);
    const first = validateCluster();
    if (Object.keys(first).length > 0) {
      setErrors(first);
      setStep(1);
      return;
    }
    if (endpoint.trim() && !URL.canParse(endpoint.trim())) {
      setErrors({ endpoint: t("endpointInvalid") });
      return;
    }
    let authConfig: unknown = {};
    try {
      authConfig = JSON.parse(authConfigText.trim() || "{}");
    } catch {
      // A JavaScript parse error may echo credentials from the input. Keep local
      // validation generic; server diagnostics are still displayed unchanged.
      setErrors({ authConfig: t("authConfigInvalid") });
      return;
    }
    let result: RegisterResult;
    try {
      result = await onRegister({
        name: name.trim(),
        slug: slug || slugify(name),
        providerPluginSlug: pluginSlug,
        authMethod,
        region: region.trim() || null,
        endpoint: endpoint.trim() || null,
        ingressClass: ingressClass.trim() || "nginx",
        authConfig,
      });
    } catch (err) {
      setFormError(err instanceof Error ? err.message : t("failed"));
      return;
    }
    if (result.ok) return;
    setErrors(result.fieldErrors);
    setFormError(result.formError);
    const steps = Object.keys(result.fieldErrors).map((f) => STEP_OF[f as RegisterField]);
    if (steps.includes(1)) setStep(1);
  }

  const noPlugins = plugins.length === 0;

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-6">
      <ShellHeader
        crumbs={localizedClusterCrumbs(chrome, t("title"))}
        title={t("title")}
        context={
          <ol aria-label={t("steps")} className="inline-flex flex-wrap items-center gap-2">
            {STEPS.map((s, i) => (
              <li
                key={s.n}
                aria-current={step === s.n ? "step" : undefined}
                className={cn(
                  "inline-flex items-center gap-1.5",
                  step === s.n ? "text-foreground font-medium" : "text-muted-foreground"
                )}
              >
                {i > 0 && (
                  <span aria-hidden className="text-muted-foreground">
                    ·
                  </span>
                )}
                <span className="font-mono">{s.n}</span> {t(s.key)}
              </li>
            ))}
          </ol>
        }
      />

      <p className="text-muted-foreground max-w-2xl text-sm">{t("description")}</p>

      <form
        onSubmit={step === 1 ? next : submit}
        noValidate
        className="bg-card flex max-w-3xl min-w-0 flex-col gap-4 rounded-md border p-6"
      >
        {formError && (
          <div
            role="alert"
            className="border-destructive/40 bg-destructive/5 text-destructive flex min-w-0 items-start gap-2 rounded-md border p-3 text-sm"
          >
            <AlertTriangleIcon className="mt-0.5 size-4 shrink-0" />
            <span className="min-w-0 [overflow-wrap:anywhere]">{formError}</span>
          </div>
        )}

        {step === 1 ? (
          <React.Fragment key="cluster">
            <div className="grid min-w-0 gap-4 sm:grid-cols-2">
              <Field id="name" label={t("name")} error={errors.name}>
                <Input
                  id="name"
                  value={name}
                  onChange={(e) => {
                    setName(e.target.value);
                    clear("name");
                    if (!slugTouched) setSlug(slugify(e.target.value));
                  }}
                  placeholder="prd-us-west-2-tenant"
                  {...invalid("name", errors.name)}
                />
              </Field>
              <Field id="slug" label={t("slug")} error={errors.slug}>
                <Input
                  id="slug"
                  value={slug}
                  onChange={(e) => {
                    setSlug(e.target.value);
                    setSlugTouched(true);
                    clear("slug");
                  }}
                  className="font-mono text-xs"
                  {...invalid("slug", errors.slug)}
                />
              </Field>
            </div>
            <div className="grid min-w-0 gap-4 sm:grid-cols-2">
              <Field
                id="plugin"
                label={t("plugin")}
                error={errors.providerPluginSlug ?? (noPlugins ? t("noPlugins") : undefined)}
              >
                <Select
                  value={pluginSlug}
                  onValueChange={(v) => {
                    onPluginSlugChange(v);
                    clear("providerPluginSlug");
                  }}
                >
                  <SelectTrigger
                    id="plugin"
                    className="w-full min-w-0"
                    {...invalid("plugin", errors.providerPluginSlug)}
                  >
                    <SelectValue
                      placeholder={noPlugins ? t("noPluginsPlaceholder") : t("selectPlugin")}
                    />
                  </SelectTrigger>
                  <SelectContent>
                    {plugins.map((p) => (
                      <SelectItem key={p.slug} value={p.slug}>
                        {p.name}{" "}
                        <span className="text-muted-foreground font-mono text-xs">{p.slug}</span>
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
              <RegionPicker
                pluginSlug={pluginSlug}
                value={region}
                onChange={(v) => {
                  setRegion(v);
                  clear("region");
                }}
                regions={regions}
                loading={regionsLoading}
                error={errors.region}
              />
            </div>
          </React.Fragment>
        ) : (
          <React.Fragment key="connection">
            <Field id="endpoint" label={t("endpoint")} error={errors.endpoint}>
              <Input
                id="endpoint"
                value={endpoint}
                onChange={(e) => {
                  setEndpoint(e.target.value);
                  clear("endpoint");
                }}
                type="url"
                placeholder="https://kube.example.com"
                className="font-mono text-xs"
                {...invalid("endpoint", errors.endpoint)}
              />
            </Field>
            <div className="grid min-w-0 gap-4 sm:grid-cols-2">
              <Field id="auth-method" label={t("authMethod")} error={errors.authMethod}>
                <Select value={authMethod} onValueChange={handleAuthMethodChange}>
                  <SelectTrigger id="auth-method" className="w-full min-w-0 font-mono">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="kubeconfig" className="font-mono">
                      kubeconfig
                    </SelectItem>
                    <SelectItem value="exec_plugin" className="font-mono">
                      exec_plugin
                    </SelectItem>
                    <SelectItem value="service_account_token" className="font-mono">
                      service_account_token
                    </SelectItem>
                  </SelectContent>
                </Select>
              </Field>
              <Field id="ingress" label={t("ingressClass")} error={errors.ingressClass}>
                <Input
                  id="ingress"
                  value={ingressClass}
                  onChange={(e) => {
                    setIngressClass(e.target.value);
                    clear("ingressClass");
                  }}
                  className="font-mono text-xs"
                  {...invalid("ingress", errors.ingressClass)}
                />
              </Field>
            </div>
            {/* Method-aware guidance (#902): exact keys per method, seeded as an
                example, so operators don't paste a valid-but-wrong shape. */}
            <Field
              id="auth-config"
              label={t("authConfig")}
              error={errors.authConfig}
              help={authConfigHint(authMethod, t)}
            >
              <Textarea
                id="auth-config"
                value={authConfigText}
                onChange={(e) => {
                  setAuthConfigText(e.target.value);
                  setAuthConfigTouched(true);
                  clear("authConfig");
                }}
                rows={6}
                className="font-mono text-xs"
                {...invalid("auth-config", errors.authConfig)}
              />
            </Field>

            {/* Paste-kubeconfig transform: raw multi-line YAML → escaped JSON
                envelope, so operators don't hand-escape newlines/quotes. */}
            {authMethod === "kubeconfig" && (
              <div className="border-border min-w-0 space-y-2 rounded-md border border-dashed p-3">
                <Label htmlFor="raw-kubeconfig" className="text-xs">
                  {t("pasteKubeconfig")}
                </Label>
                <Textarea
                  id="raw-kubeconfig"
                  value={rawKubeconfig}
                  onChange={(e) => setRawKubeconfig(e.target.value)}
                  rows={4}
                  placeholder={"apiVersion: v1\nkind: Config\nclusters:\n  - ..."}
                  className="font-mono text-xs"
                />
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={!rawKubeconfig.trim()}
                  onClick={applyRawKubeconfig}
                >
                  {t("convertAuthConfig")}
                </Button>
              </div>
            )}
          </React.Fragment>
        )}

        <div className="flex min-w-0 flex-wrap items-center justify-end gap-2 border-t pt-4">
          <Button type="button" variant="ghost" asChild className="mr-auto">
            <Link href={cancelHref}>{t("cancel")}</Link>
          </Button>
          {step === 2 && (
            <Button type="button" variant="outline" onClick={() => setStep(1)}>
              <ArrowLeftIcon className="size-4" />
              {t("back")}
            </Button>
          )}
          {step === 1 ? (
            <Button type="submit" disabled={noPlugins}>
              {t("continue")}
              <ArrowRightIcon className="size-4" />
            </Button>
          ) : (
            <Button type="submit" disabled={registering || noPlugins}>
              {registering ? t("registering") : t("title")}
            </Button>
          )}
        </div>
      </form>
    </div>
  );
}

// Region picker (#860). Takes the selected provider's regions and offers
// them as a searchable combobox while still accepting free-text entry — the
// typed input value *is* the region, and picking a suggestion just fills the
// slug. This keeps the old "type any region" escape hatch (cross-account,
// brand-new, or not-yet-in-our-table regions) working when the live driver
// list is empty or the query errors.
//
// k8s_native has no region concept, so the field is hidden entirely (the
// backend resolver returns [] for it anyway). The mutation already sends
// `region: null` when the string is empty, so a hidden field registers a
// region-less cluster cleanly.
function RegionPicker({
  pluginSlug,
  value,
  onChange,
  regions,
  loading,
  error,
}: {
  pluginSlug: string;
  value: string;
  onChange: (v: string) => void;
  regions: ProviderRegion[];
  loading: boolean;
  error?: string;
}) {
  const t = useTranslations("clusters.registration");
  if (pluginSlug === "k8s_native") return null;

  const selected = regions.find((r) => r.id === value) ?? null;

  return (
    <Field id="region" label={t("region")} error={error} help={t("regionHelp")}>
      <Combobox<ProviderRegion>
        items={regions}
        itemToStringLabel={(r) => r.id}
        value={selected}
        onValueChange={(v) => {
          if (v && typeof v === "object" && "id" in v) onChange(v.id);
        }}
        inputValue={value}
        onInputValueChange={(v) => onChange(v ?? "")}
      >
        <ComboboxInput
          id="region"
          placeholder={loading ? t("regionsLoading") : "us-west-2"}
          className="font-mono text-xs"
          {...invalid("region", error)}
        />
        <ComboboxContent>
          <ComboboxEmpty>
            {value ? t("regionCustom", { region: value }) : t("regionsEmpty")}
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
    </Field>
  );
}
