"use client";

import {
  BoxIcon,
  ChevronRightIcon,
  EyeIcon,
  EyeOffIcon,
  KeyRoundIcon,
  PlusIcon,
  RefreshCwIcon,
  Trash2Icon,
  UnplugIcon,
} from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
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

import { ProjectManagedResourceList } from "./ProjectManagedResourceList";
import { ManagedResourceDetail } from "./ManagedResourceDetail";

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
  resourceTable,
  resourceDetail,
  onOpenResource,
  bundles,
  agents,
  projectAppEnvironments,
  effectiveClusterId,
  effectiveClusterSlug,
  onClusterChange,
  effectiveBundleClusterId,
  onBundleClusterChange,
  catalogEntries,
  catalogLoading,
  catalogError,
  revealed,
  onToggleReveal,
  consumerBundle,
  setConsumerBundle,
  provisioning,
  onProvision,
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
  const resourceCopy = useTranslations("projectResources.reads");
  const t = useTranslations("projectResourcesScreen");
  const confirmation = useTranslations("shared.confirmation");
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
  const [bundleSlugTouched, setBundleSlugTouched] = React.useState(false);
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
      <PageShell title={resourceCopy("title")}>
        <Skeleton className="h-80" />
      </PageShell>
    );
  }
  if (!project) {
    return (
      <PageShell title={t("notFound")}>
        <EmptyState
          icon={<BoxIcon className="size-5" />}
          title={t("notFound")}
          description={t("notFoundDescription")}
        />
      </PageShell>
    );
  }

  const activeServices = resourceTable.rows.filter((row) => row.status === "active").length;
  const failedServices = resourceTable.rows.filter((row) => row.status === "failed").length;

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
    setBundleSlugTouched(false);
  }

  async function setBundleKey(bundleId: string) {
    const entry = keyInputs[bundleId];
    if (!entry?.key || !entry.value) return;
    if (!(await onSetBundleKey(bundleId, entry.key, entry.value))) return;
    setKeyInputs((current) => ({ ...current, [bundleId]: { key: "", value: "" } }));
  }

  return (
    <PageShell
      title={resourceCopy("title")}
      description={
        <span className="text-muted-foreground flex items-center gap-1 text-sm">
          <Link href={`/projects/${encodeURIComponent(slug)}`} className="hover:underline">
            {project.name}
          </Link>
          <ChevronRightIcon className="size-3" />
          <span className="text-foreground">{t("breadcrumb")}</span>
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
              <KeyRoundIcon className="size-4" /> {t("newBundle")}
            </Button>
            <Button onClick={() => setResourceOpen(true)}>
              <PlusIcon className="size-4" /> {t("addResource")}
            </Button>
          </>
        ) : null
      }
    >
      <div className="grid gap-4 sm:grid-cols-3">
        <StatTile
          icon={BoxIcon}
          label={t("managedResources")}
          value={resourceTable.totalCount ?? "—"}
          footer={resourceCopy("activeOnPage", { count: activeServices })}
        />
        <StatTile
          icon={KeyRoundIcon}
          label={t("bundles")}
          value={bundles.length}
          footer={t("keyCount", { count: bundles.reduce((sum, row) => sum + row.keyCount, 0) })}
        />
        <StatTile
          icon={RefreshCwIcon}
          label={t("needsAttention")}
          value={failedServices}
          footer={resourceCopy("failedOnPage")}
          className={failedServices ? "border-warning-border" : undefined}
        />
      </div>

      <SettingsPage
        single={section}
        sections={[
          {
            id: "infrastructure",
            title: t("infrastructure"),
            content: (
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">{t("infrastructure")}</CardTitle>
                  <CardDescription>{t("infrastructureDescription")}</CardDescription>
                </CardHeader>
                <CardContent>
                  <ProjectManagedResourceList controller={resourceTable} onOpen={onOpenResource} />
                </CardContent>
              </Card>
            ),
          },
          {
            id: "bundles",
            title: t("bundles"),
            content: (
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">{t("bundles")}</CardTitle>
                  <CardDescription>{t("bundlesDescription")}</CardDescription>
                </CardHeader>
                <CardContent className="space-y-3">
                  {bundles.length === 0 ? (
                    <div className="text-muted-foreground rounded-lg border border-dashed p-8 text-center text-sm">
                      {t("noBundles")}
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
                                  <UnplugIcon className="size-3" /> {t("consumers")}
                                </Button>
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  className="text-danger-fg"
                                  onClick={() => onDeleteBundle(bundle)}
                                >
                                  <Trash2Icon className="size-3" /> {t("delete")}
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
                              <span className="text-muted-foreground text-xs">{t("noKeys")}</span>
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
                                placeholder={t("secretValue")}
                                value={entry.value}
                                onChange={(event) =>
                                  setKeyInputs((current) => ({
                                    ...current,
                                    [bundle.id]: { ...entry, value: event.target.value },
                                  }))
                                }
                              />
                              <Button onClick={() => setBundleKey(bundle.id)}>{t("setKey")}</Button>
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
              <DialogTitle>{t("addTitle")}</DialogTitle>
              <DialogDescription>{t("addDescription")}</DialogDescription>
            </DialogHeader>
            <div className="space-y-2">
              <Label htmlFor="resource-kind">{t("providerResource")}</Label>
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
                {catalogEntries.length === 0 && <option value="">{t("noCatalogEntries")}</option>}
                {catalogEntries.map((entry) => (
                  <option key={entry.id} value={entry.id}>
                    {entry.kind.replaceAll("_", " ")} · {entry.displayName} · {entry.variant}
                    {!entry.available ? ` (${entry.status})` : ""}
                  </option>
                ))}
              </select>
              {catalogLoading && (
                <p className="text-muted-foreground text-xs">{t("catalogLoading")}</p>
              )}
              {catalogError && <p className="text-danger-fg text-xs">{t("catalogError")}</p>}
              {selectedCatalogEntry && (
                <div className="bg-muted/40 space-y-2 rounded-md border p-3 text-xs">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant="outline">{selectedCatalogEntry.providerPluginSlug}</Badge>
                    <Badge variant="secondary">{selectedCatalogEntry.status}</Badge>
                    {selectedCatalogEntry.isDefaultForKind && <Badge>{t("default")}</Badge>}
                  </div>
                  <p className="text-muted-foreground">{selectedCatalogEntry.description}</p>
                  {!selectedCatalogEntry.available && (
                    <p className="text-warning-fg">
                      {selectedCatalogEntry.unavailableReason || t("capabilityUnavailable")}
                    </p>
                  )}
                  {selectedCatalogEntry.issueUrl && (
                    <a
                      href={selectedCatalogEntry.issueUrl}
                      target="_blank"
                      rel="noreferrer"
                      className="text-primary inline-flex underline-offset-4 hover:underline"
                    >
                      {t("viewIssue")}
                    </a>
                  )}
                  {selectedCatalogEntry.bindingEnvs.length > 0 && (
                    <p className="text-muted-foreground">
                      {t("bindings", { envs: selectedCatalogEntry.bindingEnvs.join(", ") })}
                    </p>
                  )}
                </div>
              )}
            </div>
            <div className="space-y-2">
              <Label htmlFor="resource-name">{resourceCopy("name")}</Label>
              <Input
                id="resource-name"
                value={resourceName}
                onChange={(event) => setResourceName(event.target.value)}
                placeholder={selectedCatalogEntry?.kind ?? "resource"}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="resource-cluster">{resourceCopy("cluster")}</Label>
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
                <Label htmlFor="resource-size">{t("sizePreset")}</Label>
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
                <legend className="px-1 text-sm font-medium">{t("providerOptions")}</legend>
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
                            <option value="">{t("providerDefault")}</option>
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
                  <Label htmlFor="resource-advanced-config">{t("advancedConfig")}</Label>
                  <Textarea
                    id="resource-advanced-config"
                    className="min-h-24 font-mono text-xs"
                    value={resourceAdvancedConfig}
                    onChange={(event) => setResourceAdvancedConfig(event.target.value)}
                  />
                  <p className="text-muted-foreground text-xs">{t("advancedConfigHint")}</p>
                </div>
              </fieldset>
            )}
            <fieldset className="space-y-2">
              <legend className="text-sm font-medium">{t("attachAgents")}</legend>
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
              <legend className="text-sm font-medium">{t("attachAppEnvironments")}</legend>
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
                {confirmation("cancel")}
              </Button>
              <Button
                type="submit"
                disabled={!effectiveClusterId || !selectedCatalogEntry?.available || provisioning}
              >
                {t("provision")}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <ManagedResourceDetail
        key={
          resourceDetail.target
            ? `${resourceDetail.target.id}:${resourceDetail.target.contextRevision}`
            : "closed"
        }
        {...resourceDetail}
      />

      <Dialog
        open={consumerBundle !== null}
        onOpenChange={(open) => {
          if (!open) setConsumerBundle(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("consumersTitle")}</DialogTitle>
            <DialogDescription>
              {t("consumersDescription", { name: consumerBundle?.name ?? "" })}
            </DialogDescription>
          </DialogHeader>
          {consumerBundle && (
            <div className="max-h-[65vh] space-y-5 overflow-y-auto pr-1">
              <div className="space-y-2">
                <Label>{t("attached")}</Label>
                {consumerBundle.consumers.length === 0 ? (
                  <p className="text-muted-foreground text-sm">{t("noConsumers")}</p>
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
                        <Trash2Icon className="size-3" /> {resourceCopy("detach")}
                      </Button>
                    </div>
                  ))
                )}
              </div>
              <div className="space-y-2">
                <Label>{t("availableAgents")}</Label>
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
                        {resourceCopy("attach")}
                      </Button>
                    </div>
                  ))}
              </div>
              <div className="space-y-2">
                <Label>{t("availableAppEnvironments")}</Label>
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
                        {resourceCopy("attach")}
                      </Button>
                    </div>
                  ))}
              </div>
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setConsumerBundle(null)}>
              {t("done")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={bundleOpen} onOpenChange={setBundleOpen}>
        <DialogContent>
          <form onSubmit={submitBundle} className="space-y-4">
            <DialogHeader>
              <DialogTitle>{t("newBundleTitle")}</DialogTitle>
              <DialogDescription>{t("newBundleDescription")}</DialogDescription>
            </DialogHeader>
            <div className="space-y-2">
              <Label htmlFor="bundle-name">{resourceCopy("name")}</Label>
              <Input
                id="bundle-name"
                required
                value={bundleName}
                onChange={(event) => {
                  setBundleName(event.target.value);
                  if (!bundleSlugTouched)
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
              <Label htmlFor="bundle-slug">{t("slug")}</Label>
              <Input
                id="bundle-slug"
                required
                value={bundleSlug}
                onChange={(event) => {
                  setBundleSlugTouched(true);
                  setBundleSlug(event.target.value);
                }}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="bundle-cluster">{resourceCopy("cluster")}</Label>
              <select
                id="bundle-cluster"
                required
                className="border-input bg-background h-9 w-full rounded-md border px-3 text-sm"
                value={effectiveBundleClusterId}
                onChange={(event) => onBundleClusterChange(event.target.value)}
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
                {confirmation("cancel")}
              </Button>
              <Button type="submit" disabled={!effectiveBundleClusterId || creatingBundle}>
                {t("createBundle")}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </PageShell>
  );
}
