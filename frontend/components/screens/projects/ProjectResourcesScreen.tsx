"use client";

import {
  BoxIcon,
  ChevronRightIcon,
  CircleDollarSignIcon,
  EyeIcon,
  EyeOffIcon,
  ExternalLinkIcon,
  KeyRoundIcon,
  PlusIcon,
  RefreshCwIcon,
  Trash2Icon,
  UnplugIcon,
} from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { SettingsPage } from "@/components/settings/SettingsPage";
import type { SectionSelection } from "@/components/settings/use-settings-section";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
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
import { Skeleton } from "@/components/ui/skeleton";
import { StatTile } from "@/components/ui/stat-tile";
import { Textarea } from "@/components/ui/textarea";

import type { useProjectResources } from "./use-project-resources";

export type ProjectResourcesScreenProps = ReturnType<typeof useProjectResources> & {
  /**
   * Which list is shown (`?section=`): managed infrastructure or shared
   * secret bundles, one at a time (list rule 3). Without it both render.
   */
  section?: SectionSelection;
};

/**
 * Project resources: managed infrastructure and shared secret bundles, one
 * section at a time, with the Add resource, New bundle, and consumer
 * dialogs. Pure view; the
 * form fields live here, everything that talks to the server comes from
 * useProjectResources.
 */
export function ProjectResourcesScreen({
  slug,
  project,
  loading,
  canUpdate,
  canWriteSecrets,
  canReadSecrets,
  clusters,
  services,
  bundles,
  resourcesLoading,
  agents,
  projectAppEnvironments,
  effectiveClusterId,
  effectiveClusterSlug,
  onClusterChange,
  catalogEntries,
  catalogLoading,
  catalogError,
  costPreviews,
  costPreviewLoading,
  onCostPreview,
  revealed,
  onToggleReveal,
  consumerService,
  setConsumerService,
  consumerBundle,
  setConsumerBundle,
  provisioning,
  onProvision,
  onReprovision,
  onDeprovision,
  attachingConsumer,
  detachingConsumer,
  onAttachServiceConsumer,
  onDetachServiceConsumer,
  creatingBundle,
  onCreateBundle,
  onSetBundleKey,
  onDeleteBundleKey,
  onDeleteBundle,
  attachingAgentBundle,
  attachingAppBundle,
  detachingBundleConsumer,
  onAttachBundleToAgent,
  onAttachBundleToApp,
  onDetachBundleConsumer,
  section,
}: ProjectResourcesScreenProps) {
  const [resourceOpen, setResourceOpen] = React.useState(false);
  const [bundleOpen, setBundleOpen] = React.useState(false);
  const [resourceCatalogId, setResourceCatalogId] = React.useState("");
  const [resourceName, setResourceName] = React.useState("");
  const [resourceSize, setResourceSize] = React.useState("small");
  const [resourceConfig, setResourceConfig] = React.useState<Record<string, unknown>>({});
  const [resourceAdvancedConfig, setResourceAdvancedConfig] = React.useState("{}");
  const [selectedAgents, setSelectedAgents] = React.useState<string[]>([]);
  const [selectedAppEnvironments, setSelectedAppEnvironments] = React.useState<string[]>([]);
  const [bundleName, setBundleName] = React.useState("");
  const [bundleSlug, setBundleSlug] = React.useState("");
  const [keyInputs, setKeyInputs] = React.useState<Record<string, { key: string; value: string }>>(
    {}
  );

  const selectedCatalogEntry =
    catalogEntries.find((row) => row.id === resourceCatalogId) ??
    catalogEntries.find((row) => row.available && row.isDefaultForKind) ??
    catalogEntries.find((row) => row.available) ??
    catalogEntries[0];

  if (loading) {
    return (
      <PageShell title="Project resources">
        <Skeleton className="h-80" />
      </PageShell>
    );
  }
  if (!project) {
    return (
      <PageShell title="Project not found">
        <EmptyState
          icon={<BoxIcon className="size-5" />}
          title="Project not found"
          description="This project is unavailable."
        />
      </PageShell>
    );
  }

  const activeServices = services.filter((row) => row.status === "active").length;
  const failedServices = services.filter((row) => row.status === "failed").length;

  async function submitResource(event: React.FormEvent) {
    event.preventDefault();
    const started = await onProvision({
      entry: selectedCatalogEntry,
      name: resourceName,
      size: resourceSize,
      config: resourceConfig,
      advancedConfig: resourceAdvancedConfig,
      agentSlugs: selectedAgents,
      appEnvironmentIds: selectedAppEnvironments,
    });
    if (!started) return;
    setResourceOpen(false);
    setResourceName("");
    setResourceConfig({});
    setResourceAdvancedConfig("{}");
    setSelectedAgents([]);
    setSelectedAppEnvironments([]);
  }

  async function submitBundle(event: React.FormEvent) {
    event.preventDefault();
    if (!(await onCreateBundle(bundleName, bundleSlug))) return;
    setBundleOpen(false);
    setBundleName("");
    setBundleSlug("");
  }

  async function setBundleKey(bundleId: string) {
    const entry = keyInputs[bundleId];
    if (!entry?.key || !entry.value) return;
    if (!(await onSetBundleKey(bundleId, entry.key, entry.value))) return;
    setKeyInputs((current) => ({ ...current, [bundleId]: { key: "", value: "" } }));
  }

  return (
    <PageShell
      title="Project resources"
      description={
        <span className="text-muted-foreground flex items-center gap-1 text-sm">
          <Link href={`/projects/${encodeURIComponent(slug)}`} className="hover:underline">
            {project.name}
          </Link>
          <ChevronRightIcon className="size-3" />
          <span className="text-foreground">Resources</span>
        </span>
      }
      actions={
        canUpdate ? (
          <>
            <Button
              variant="outline"
              onClick={() => setBundleOpen(true)}
              disabled={!canWriteSecrets}
            >
              <KeyRoundIcon className="size-4" /> New secret bundle
            </Button>
            <Button onClick={() => setResourceOpen(true)}>
              <PlusIcon className="size-4" /> Add resource
            </Button>
          </>
        ) : null
      }
    >
      <div className="grid gap-4 sm:grid-cols-3">
        <StatTile
          icon={BoxIcon}
          label="Managed resources"
          value={services.length}
          footer={`${activeServices} active`}
        />
        <StatTile
          icon={KeyRoundIcon}
          label="Shared secret bundles"
          value={bundles.length}
          footer={`${bundles.reduce((sum, row) => sum + row.keyCount, 0)} keys`}
        />
        <StatTile
          icon={RefreshCwIcon}
          label="Needs attention"
          value={failedServices}
          footer="failed provisioning or lifecycle operations"
          className={failedServices ? "border-warning-border" : undefined}
        />
      </div>

      <SettingsPage
        single={section}
        sections={[
          {
            id: "infrastructure",
            title: "Managed infrastructure",
            content: (
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">Managed infrastructure</CardTitle>
                  <CardDescription>
                    Project-owned databases, caches, indexes, buckets, and queues can be shared by
                    multiple apps and workflow agents.
                  </CardDescription>
                </CardHeader>
                <CardContent className="grid gap-3 md:grid-cols-2">
                  {resourcesLoading ? (
                    <Skeleton className="col-span-full h-32" />
                  ) : services.length === 0 ? (
                    <div className="text-muted-foreground col-span-full rounded-lg border border-dashed p-8 text-center text-sm">
                      No project resources yet.
                    </div>
                  ) : (
                    services.map((service) => (
                      <article key={service.id} className="rounded-lg border p-4">
                        <div className="flex items-start justify-between gap-3">
                          <div>
                            <h3 className="font-medium">{service.name}</h3>
                            <p className="text-muted-foreground mt-1 text-xs">
                              {service.kind.replace(/_/g, " ")} · {service.clusterSlug}
                            </p>
                          </div>
                          <Badge
                            variant={service.status === "failed" ? "destructive" : "secondary"}
                            className="capitalize"
                          >
                            {service.status}
                          </Badge>
                        </div>
                        {service.statusError && (
                          <p className="text-danger-fg mt-2 text-xs">{service.statusError}</p>
                        )}
                        {service.operationKind && (
                          <div className="text-muted-foreground mt-2 space-y-0.5 text-xs">
                            <p>
                              Last operation:{" "}
                              <span className="font-medium">{service.operationKind}</span>
                              {service.operationCompletedAt
                                ? ` · completed ${new Date(service.operationCompletedAt).toLocaleString()}`
                                : " · running"}
                            </p>
                            {service.operationWorkflowId && (
                              <p className="truncate font-mono" title={service.operationWorkflowId}>
                                {service.operationWorkflowId}
                                {service.operationRunId ? ` · ${service.operationRunId}` : ""}
                              </p>
                            )}
                          </div>
                        )}
                        <div className="mt-3 flex flex-wrap gap-1">
                          {service.attachments.map((attachment) => (
                            <Badge key={attachment.id} variant="outline">
                              {attachment.consumerSlug}
                            </Badge>
                          ))}
                          {service.attachments.length === 0 && (
                            <span className="text-muted-foreground text-xs">Not attached yet</span>
                          )}
                        </div>
                        {service.volumeBindings.length > 0 && (
                          <div className="bg-muted/30 mt-3 space-y-2 rounded-md border p-3">
                            <p className="text-muted-foreground text-2xs font-medium tracking-wide uppercase">
                              Runtime mounts
                            </p>
                            {service.volumeBindings.map((binding) => (
                              <div
                                key={binding.id}
                                className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs"
                              >
                                <code>{binding.mountPath}</code>
                                <Badge variant="outline">{binding.protocol}</Badge>
                                <span className="text-muted-foreground">
                                  {binding.sourceKind === "csi"
                                    ? binding.csiDriver
                                    : binding.sourceKind === "dynamic_pvc"
                                      ? `StorageClass ${binding.storageClassName}`
                                      : `${binding.claimNamespace}/${binding.claimName}`}
                                </span>
                                {binding.readOnly && <Badge variant="secondary">read only</Badge>}
                                {binding.credentialReferenceCount > 0 && (
                                  <span className="text-muted-foreground">
                                    {binding.credentialReferenceCount} credential refs
                                  </span>
                                )}
                              </div>
                            ))}
                          </div>
                        )}
                        {costPreviews[service.id] && (
                          <div className="bg-muted/30 mt-3 rounded-md border p-3 text-xs">
                            {costPreviews[service.id].available ? (
                              <>
                                <span className="font-medium">
                                  {costPreviews[service.id].approximate ? "Approx. " : ""}
                                  {new Intl.NumberFormat(undefined, {
                                    style: "currency",
                                    currency: costPreviews[service.id].currency,
                                  }).format(costPreviews[service.id].monthlyTotal ?? 0)}
                                  /month
                                </span>
                                {costPreviews[service.id].pricingSourceUrl && (
                                  <a
                                    className="ml-2 underline underline-offset-2"
                                    href={costPreviews[service.id].pricingSourceUrl}
                                    target="_blank"
                                    rel="noreferrer"
                                  >
                                    pricing source
                                  </a>
                                )}
                                {costPreviews[service.id].notes[0] && (
                                  <p className="text-muted-foreground mt-1">
                                    {costPreviews[service.id].notes[0]}
                                  </p>
                                )}
                              </>
                            ) : (
                              <span className="text-muted-foreground">
                                Cost unavailable:{" "}
                                {costPreviews[service.id].message ||
                                  costPreviews[service.id].reason}
                              </span>
                            )}
                          </div>
                        )}
                        <div className="mt-3 flex flex-wrap gap-2">
                          <Button
                            size="sm"
                            variant="outline"
                            disabled={costPreviewLoading}
                            onClick={() => onCostPreview(service.id)}
                          >
                            <CircleDollarSignIcon className="size-3" /> Cost preview
                          </Button>
                          {service.providerPortalUrl && (
                            <Button size="sm" variant="outline" asChild>
                              <a href={service.providerPortalUrl} target="_blank" rel="noreferrer">
                                <ExternalLinkIcon className="size-3" /> Provider portal
                              </a>
                            </Button>
                          )}
                        </div>
                        {canUpdate && (
                          <div className="mt-3 flex justify-end gap-2">
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() => setConsumerService(service)}
                            >
                              <UnplugIcon className="size-3" /> Consumers
                            </Button>
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() => onReprovision(service.id)}
                            >
                              <RefreshCwIcon className="size-3" /> Reprovision
                            </Button>
                            <Button
                              size="sm"
                              variant="ghost"
                              className="text-danger-fg"
                              onClick={() => onDeprovision(service)}
                            >
                              <Trash2Icon className="size-3" /> Deprovision
                            </Button>
                          </div>
                        )}
                      </article>
                    ))
                  )}
                </CardContent>
              </Card>
            ),
          },
          {
            id: "bundles",
            title: "Shared secret bundles",
            content: (
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">Shared secret bundles</CardTitle>
                  <CardDescription>
                    Project-scoped credentials that can be attached to selected apps and agent
                    environments. Values are stored only in the cluster secrets backend.
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-3">
                  {bundles.length === 0 ? (
                    <div className="text-muted-foreground rounded-lg border border-dashed p-8 text-center text-sm">
                      No shared secret bundles yet.
                    </div>
                  ) : (
                    bundles.map((bundle) => {
                      const entry = keyInputs[bundle.id] ?? { key: "", value: "" };
                      return (
                        <section key={bundle.id} className="rounded-lg border p-4">
                          <div className="flex items-start justify-between gap-3">
                            <div>
                              <h3 className="font-medium">{bundle.name}</h3>
                              <p className="text-muted-foreground font-mono text-xs">
                                {bundle.slug} · {bundle.clusterSlug}
                              </p>
                            </div>
                            {canWriteSecrets && (
                              <div className="flex items-center gap-1">
                                <Button
                                  size="sm"
                                  variant="outline"
                                  onClick={() => setConsumerBundle(bundle)}
                                >
                                  <UnplugIcon className="size-3" /> Consumers
                                </Button>
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  className="text-danger-fg"
                                  onClick={() => onDeleteBundle(bundle)}
                                >
                                  <Trash2Icon className="size-3" /> Delete
                                </Button>
                              </div>
                            )}
                          </div>
                          <div className="mt-3 flex flex-wrap gap-2">
                            {bundle.keyNames.map((key) => (
                              <div
                                key={key}
                                className="flex items-center gap-1 rounded-md border px-2 py-1 text-xs"
                              >
                                <span className="font-mono">{key}</span>
                                {revealed[`${bundle.id}:${key}`] && (
                                  <code className="text-danger-fg max-w-48 truncate">
                                    {revealed[`${bundle.id}:${key}`]}
                                  </code>
                                )}
                                {canReadSecrets && (
                                  <Button
                                    size="icon"
                                    variant="ghost"
                                    className="size-6"
                                    onClick={() => onToggleReveal(bundle.id, key)}
                                  >
                                    {revealed[`${bundle.id}:${key}`] ? (
                                      <EyeOffIcon className="size-3" />
                                    ) : (
                                      <EyeIcon className="size-3" />
                                    )}
                                  </Button>
                                )}
                                {canWriteSecrets && (
                                  <Button
                                    size="icon"
                                    variant="ghost"
                                    className="text-danger-fg size-6"
                                    onClick={() => onDeleteBundleKey(bundle.id, key)}
                                  >
                                    <Trash2Icon className="size-3" />
                                  </Button>
                                )}
                              </div>
                            ))}
                            {bundle.keyNames.length === 0 && (
                              <span className="text-muted-foreground text-xs">No keys yet</span>
                            )}
                          </div>
                          {canWriteSecrets && (
                            <div className="mt-3 grid gap-2 sm:grid-cols-[1fr_2fr_auto]">
                              <Input
                                placeholder="KEY_NAME"
                                value={entry.key}
                                onChange={(event) =>
                                  setKeyInputs((current) => ({
                                    ...current,
                                    [bundle.id]: { ...entry, key: event.target.value },
                                  }))
                                }
                              />
                              <Input
                                type="password"
                                placeholder="Secret value"
                                value={entry.value}
                                onChange={(event) =>
                                  setKeyInputs((current) => ({
                                    ...current,
                                    [bundle.id]: { ...entry, value: event.target.value },
                                  }))
                                }
                              />
                              <Button onClick={() => setBundleKey(bundle.id)}>Set key</Button>
                            </div>
                          )}
                        </section>
                      );
                    })
                  )}
                </CardContent>
              </Card>
            ),
          },
        ]}
      />

      <Dialog open={resourceOpen} onOpenChange={setResourceOpen}>
        <DialogContent>
          <form onSubmit={submitResource} className="space-y-4">
            <DialogHeader>
              <DialogTitle>Add project resource</DialogTitle>
              <DialogDescription>
                Provision shared infrastructure once, then attach it to selected app environments
                and workflow agents in this project.
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-2">
              <Label htmlFor="resource-kind">Provider resource</Label>
              <select
                id="resource-kind"
                className="border-input bg-background h-9 w-full rounded-md border px-3 text-sm"
                value={selectedCatalogEntry?.id ?? ""}
                onChange={(event) => {
                  const next = catalogEntries.find((row) => row.id === event.target.value);
                  setResourceCatalogId(event.target.value);
                  setResourceSize(next?.sizeOptions[0] ?? "small");
                  setResourceConfig({});
                  setResourceAdvancedConfig("{}");
                }}
              >
                {catalogEntries.length === 0 && <option value="">No catalogue entries</option>}
                {catalogEntries.map((entry) => (
                  <option key={entry.id} value={entry.id}>
                    {entry.kind.replaceAll("_", " ")} · {entry.displayName} · {entry.variant}
                    {!entry.available ? ` (${entry.status})` : ""}
                  </option>
                ))}
              </select>
              {catalogLoading && (
                <p className="text-muted-foreground text-xs">Loading cluster catalogue…</p>
              )}
              {catalogError && (
                <p className="text-danger-fg text-xs">The cluster catalogue could not be loaded.</p>
              )}
              {selectedCatalogEntry && (
                <div className="bg-muted/40 space-y-2 rounded-md border p-3 text-xs">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant="outline">{selectedCatalogEntry.providerPluginSlug}</Badge>
                    <Badge variant="secondary">{selectedCatalogEntry.status}</Badge>
                    {selectedCatalogEntry.isDefaultForKind && <Badge>default</Badge>}
                  </div>
                  <p className="text-muted-foreground">{selectedCatalogEntry.description}</p>
                  {!selectedCatalogEntry.available && (
                    <p className="text-warning-fg">
                      {selectedCatalogEntry.unavailableReason ||
                        "This provider capability is not available yet."}
                    </p>
                  )}
                  {selectedCatalogEntry.issueUrl && (
                    <a
                      href={selectedCatalogEntry.issueUrl}
                      target="_blank"
                      rel="noreferrer"
                      className="text-primary inline-flex underline-offset-4 hover:underline"
                    >
                      View implementation issue
                    </a>
                  )}
                  {selectedCatalogEntry.bindingEnvs.length > 0 && (
                    <p className="text-muted-foreground">
                      Bindings: {selectedCatalogEntry.bindingEnvs.join(", ")}
                    </p>
                  )}
                </div>
              )}
            </div>
            <div className="space-y-2">
              <Label htmlFor="resource-name">Name</Label>
              <Input
                id="resource-name"
                value={resourceName}
                onChange={(event) => setResourceName(event.target.value)}
                placeholder={selectedCatalogEntry?.kind ?? "resource"}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="resource-cluster">Cluster</Label>
              <select
                id="resource-cluster"
                required
                className="border-input bg-background h-9 w-full rounded-md border px-3 text-sm"
                value={effectiveClusterId}
                onChange={(event) => {
                  onClusterChange(event.target.value);
                  setResourceCatalogId("");
                  setResourceConfig({});
                  setResourceAdvancedConfig("{}");
                  setSelectedAgents([]);
                  setSelectedAppEnvironments([]);
                }}
              >
                {clusters.map((row) => (
                  <option key={row.id} value={row.id}>
                    {row.name} · {row.region}
                  </option>
                ))}
              </select>
            </div>
            {selectedCatalogEntry?.sizeOptions.length ? (
              <div className="space-y-2">
                <Label htmlFor="resource-size">Size preset</Label>
                <select
                  id="resource-size"
                  className="border-input bg-background h-9 w-full rounded-md border px-3 text-sm"
                  value={resourceSize}
                  onChange={(event) => setResourceSize(event.target.value)}
                >
                  {selectedCatalogEntry.sizeOptions.map((size) => (
                    <option key={size} value={size}>
                      {size}
                    </option>
                  ))}
                </select>
              </div>
            ) : null}
            {selectedCatalogEntry && (
              <fieldset className="space-y-3 rounded-md border p-3">
                <legend className="px-1 text-sm font-medium">Provider options</legend>
                {Object.entries(selectedCatalogEntry.configSchema.properties ?? {})
                  .filter(
                    ([key, schema]) =>
                      key !== "size" &&
                      ["string", "integer", "number", "boolean"].includes(schema.type ?? "")
                  )
                  .map(([key, schema]) => {
                    const value = resourceConfig[key] ?? schema.default ?? "";
                    if (schema.type === "boolean") {
                      return (
                        <label key={key} className="flex items-start gap-2 text-sm">
                          <input
                            type="checkbox"
                            checked={Boolean(value)}
                            onChange={(event) =>
                              setResourceConfig((current) => ({
                                ...current,
                                [key]: event.target.checked,
                              }))
                            }
                          />
                          <span>
                            {key.replaceAll("_", " ")}
                            {schema.description && (
                              <span className="text-muted-foreground block text-xs">
                                {schema.description}
                              </span>
                            )}
                          </span>
                        </label>
                      );
                    }
                    return (
                      <div key={key} className="space-y-1">
                        <Label htmlFor={`resource-config-${key}`}>{key.replaceAll("_", " ")}</Label>
                        {schema.enum ? (
                          <select
                            id={`resource-config-${key}`}
                            className="border-input bg-background h-9 w-full rounded-md border px-3 text-sm"
                            value={String(value)}
                            onChange={(event) => {
                              const next = event.target.value;
                              setResourceConfig((current) => {
                                const updated = { ...current };
                                if (!next) delete updated[key];
                                else
                                  updated[key] =
                                    schema.type === "integer" || schema.type === "number"
                                      ? Number(next)
                                      : next;
                                return updated;
                              });
                            }}
                          >
                            <option value="">Provider default</option>
                            {schema.enum.map((option) => (
                              <option key={String(option)} value={String(option)}>
                                {String(option)}
                              </option>
                            ))}
                          </select>
                        ) : (
                          <Input
                            id={`resource-config-${key}`}
                            type={
                              schema.type === "integer" || schema.type === "number"
                                ? "number"
                                : "text"
                            }
                            min={schema.minimum}
                            max={schema.maximum}
                            value={String(value)}
                            onChange={(event) => {
                              const next = event.target.value;
                              setResourceConfig((current) => {
                                const updated = { ...current };
                                if (!next) delete updated[key];
                                else
                                  updated[key] =
                                    schema.type === "integer" || schema.type === "number"
                                      ? Number(next)
                                      : next;
                                return updated;
                              });
                            }}
                          />
                        )}
                        {schema.description && (
                          <p className="text-muted-foreground text-xs">{schema.description}</p>
                        )}
                      </div>
                    );
                  })}
                <div className="space-y-1">
                  <Label htmlFor="resource-advanced-config">Advanced config JSON</Label>
                  <Textarea
                    id="resource-advanced-config"
                    className="min-h-24 font-mono text-xs"
                    value={resourceAdvancedConfig}
                    onChange={(event) => setResourceAdvancedConfig(event.target.value)}
                  />
                  <p className="text-muted-foreground text-xs">
                    Arrays, nested objects, and provider-native overrides. Generated fields above
                    win on conflicts.
                  </p>
                </div>
              </fieldset>
            )}
            <fieldset className="space-y-2">
              <legend className="text-sm font-medium">Attach to agents</legend>
              {agents.map((agent) => (
                <label key={agent.id} className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={selectedAgents.includes(agent.slug)}
                    onChange={(event) =>
                      setSelectedAgents((current) =>
                        event.target.checked
                          ? [...current, agent.slug]
                          : current.filter((value) => value !== agent.slug)
                      )
                    }
                  />
                  {agent.name}
                </label>
              ))}
            </fieldset>
            <fieldset className="space-y-2">
              <legend className="text-sm font-medium">Attach to app environments</legend>
              {projectAppEnvironments
                .filter((env) => !effectiveClusterSlug || env.clusterSlug === effectiveClusterSlug)
                .map((env) => (
                  <label key={env.id} className="flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={selectedAppEnvironments.includes(env.id)}
                      onChange={(event) =>
                        setSelectedAppEnvironments((current) =>
                          event.target.checked
                            ? [...current, env.id]
                            : current.filter((value) => value !== env.id)
                        )
                      }
                    />
                    {env.registeredAppSlug} · {env.name}
                  </label>
                ))}
            </fieldset>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setResourceOpen(false)}>
                Cancel
              </Button>
              <Button
                type="submit"
                disabled={!effectiveClusterId || !selectedCatalogEntry?.available || provisioning}
              >
                Provision
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog
        open={consumerService !== null}
        onOpenChange={(open) => {
          if (!open) setConsumerService(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Resource consumers</DialogTitle>
            <DialogDescription>
              Attach {consumerService?.name} to multiple apps and agents inside this project.
            </DialogDescription>
          </DialogHeader>
          {consumerService && (
            <div className="max-h-[65vh] space-y-5 overflow-y-auto pr-1">
              <div className="space-y-2">
                <Label>Attached</Label>
                {consumerService.attachments.length === 0 ? (
                  <p className="text-muted-foreground text-sm">No consumers attached.</p>
                ) : (
                  consumerService.attachments.map((attachment) => (
                    <div
                      key={attachment.id}
                      className="flex items-center justify-between rounded-md border px-3 py-2 text-sm"
                    >
                      <span>
                        {attachment.consumerSlug} · {attachment.environmentName}
                      </span>
                      <Button
                        size="sm"
                        variant="ghost"
                        className="text-danger-fg"
                        disabled={detachingConsumer}
                        onClick={() => onDetachServiceConsumer(consumerService.id, attachment.id)}
                      >
                        <Trash2Icon className="size-3" /> Detach
                      </Button>
                    </div>
                  ))
                )}
              </div>
              <div className="space-y-2">
                <Label>Available agents</Label>
                {agents
                  .filter(
                    (agent) =>
                      !consumerService.attachments.some(
                        (row) => row.consumerKind === "agent" && row.consumerSlug === agent.slug
                      )
                  )
                  .map((agent) => (
                    <div
                      key={agent.id}
                      className="flex items-center justify-between rounded-md border px-3 py-2 text-sm"
                    >
                      <span>{agent.name}</span>
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={attachingConsumer}
                        onClick={() =>
                          onAttachServiceConsumer(consumerService.id, {
                            agentEnvironmentSpecSlug: agent.slug,
                            appEnvironmentId: null,
                          })
                        }
                      >
                        Attach
                      </Button>
                    </div>
                  ))}
              </div>
              <div className="space-y-2">
                <Label>Available app environments</Label>
                {projectAppEnvironments
                  .filter(
                    (env) =>
                      env.clusterSlug === consumerService.clusterSlug &&
                      !consumerService.attachments.some(
                        (row) =>
                          row.consumerKind === "app" &&
                          row.consumerSlug === env.registeredAppSlug &&
                          row.environmentName === env.name
                      )
                  )
                  .map((env) => (
                    <div
                      key={env.id}
                      className="flex items-center justify-between rounded-md border px-3 py-2 text-sm"
                    >
                      <span>
                        {env.registeredAppSlug} · {env.name}
                      </span>
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={attachingConsumer}
                        onClick={() =>
                          onAttachServiceConsumer(consumerService.id, {
                            agentEnvironmentSpecSlug: null,
                            appEnvironmentId: env.id,
                          })
                        }
                      >
                        Attach
                      </Button>
                    </div>
                  ))}
              </div>
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setConsumerService(null)}>
              Done
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={consumerBundle !== null}
        onOpenChange={(open) => {
          if (!open) setConsumerBundle(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Secret bundle consumers</DialogTitle>
            <DialogDescription>
              Attach {consumerBundle?.name} to apps and agent recipes on its secrets cluster.
            </DialogDescription>
          </DialogHeader>
          {consumerBundle && (
            <div className="max-h-[65vh] space-y-5 overflow-y-auto pr-1">
              <div className="space-y-2">
                <Label>Attached</Label>
                {consumerBundle.consumers.length === 0 ? (
                  <p className="text-muted-foreground text-sm">No consumers attached.</p>
                ) : (
                  consumerBundle.consumers.map((consumer) => (
                    <div
                      key={`${consumer.consumerKind}:${consumer.id}`}
                      className="flex items-center justify-between rounded-md border px-3 py-2 text-sm"
                    >
                      <span>
                        {consumer.consumerSlug} · {consumer.environmentName}
                      </span>
                      <Button
                        size="sm"
                        variant="ghost"
                        className="text-danger-fg"
                        disabled={detachingBundleConsumer}
                        onClick={() => onDetachBundleConsumer(consumerBundle.id, consumer)}
                      >
                        <Trash2Icon className="size-3" /> Detach
                      </Button>
                    </div>
                  ))
                )}
              </div>
              <div className="space-y-2">
                <Label>Available agents</Label>
                {agents
                  .filter(
                    (agent) =>
                      !consumerBundle.consumers.some(
                        (row) => row.consumerKind === "agent" && row.consumerSlug === agent.slug
                      )
                  )
                  .map((agent) => (
                    <div
                      key={agent.id}
                      className="flex items-center justify-between rounded-md border px-3 py-2 text-sm"
                    >
                      <span>{agent.name}</span>
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={attachingAgentBundle}
                        onClick={() => onAttachBundleToAgent(consumerBundle, agent.slug)}
                      >
                        Attach
                      </Button>
                    </div>
                  ))}
              </div>
              <div className="space-y-2">
                <Label>Available app environments</Label>
                {projectAppEnvironments
                  .filter(
                    (env) =>
                      env.clusterSlug === consumerBundle.clusterSlug &&
                      !consumerBundle.consumers.some(
                        (row) =>
                          row.consumerKind === "app" &&
                          row.consumerSlug === env.registeredAppSlug &&
                          row.environmentName === env.name
                      )
                  )
                  .map((env) => (
                    <div
                      key={env.id}
                      className="flex items-center justify-between rounded-md border px-3 py-2 text-sm"
                    >
                      <span>
                        {env.registeredAppSlug} · {env.name}
                      </span>
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={attachingAppBundle}
                        onClick={() => onAttachBundleToApp(consumerBundle, env)}
                      >
                        Attach
                      </Button>
                    </div>
                  ))}
              </div>
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setConsumerBundle(null)}>
              Done
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={bundleOpen} onOpenChange={setBundleOpen}>
        <DialogContent>
          <form onSubmit={submitBundle} className="space-y-4">
            <DialogHeader>
              <DialogTitle>New shared secret bundle</DialogTitle>
              <DialogDescription>
                Create a project-owned bundle in the selected cluster secrets backend.
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-2">
              <Label htmlFor="bundle-name">Name</Label>
              <Input
                id="bundle-name"
                required
                value={bundleName}
                onChange={(event) => {
                  setBundleName(event.target.value);
                  if (!bundleSlug)
                    setBundleSlug(
                      event.target.value
                        .toLowerCase()
                        .replace(/[^a-z0-9]+/g, "-")
                        .replace(/^-|-$/g, "")
                    );
                }}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="bundle-slug">Slug</Label>
              <Input
                id="bundle-slug"
                required
                value={bundleSlug}
                onChange={(event) => setBundleSlug(event.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="bundle-cluster">Cluster</Label>
              <select
                id="bundle-cluster"
                required
                className="border-input bg-background h-9 w-full rounded-md border px-3 text-sm"
                value={effectiveClusterId}
                onChange={(event) => onClusterChange(event.target.value)}
              >
                {clusters.map((row) => (
                  <option key={row.id} value={row.id}>
                    {row.name} · {row.region}
                  </option>
                ))}
              </select>
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setBundleOpen(false)}>
                Cancel
              </Button>
              <Button type="submit" disabled={!effectiveClusterId || creatingBundle}>
                Create bundle
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </PageShell>
  );
}
