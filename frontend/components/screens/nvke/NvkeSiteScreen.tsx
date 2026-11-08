"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { CableIcon, ClockIcon, RefreshCwIcon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { useForm, useWatch } from "react-hook-form";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { PageShell } from "@/components/PageShell";
import { type Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { useCursorTableList } from "@/components/list/use-cursor-table-list";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { DefinitionList } from "@/components/ui/definition-list";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Section } from "@/components/ui/section";
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
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";

import { NvkeNativePanel } from "./NvkeNativePanel";
import { NvkeOperatorAgentPanel } from "./NvkeOperatorAgentPanel";
import { NvkeOperatorWorkbench } from "./NvkeOperatorWorkbench";
import { NvkeTemporalPanel } from "./NvkeTemporalPanel";
import { NvkeProviderRequestPanel } from "./NvkeProviderRequestPanel";
import { NvkeMetricsPanel } from "./NvkeMetricsPanel";
import { NvkeWorkloadAccessPanel } from "./NvkeWorkloadAccessPanel";
import { connectionSchema } from "./schema";
import {
  fixedNvkeTable,
  nvkeSurfaces,
  type NvkeConnectionValues,
  type NvkeOperationView,
  type NvkeSiteScreenProps,
  type NvkeSurface,
} from "./types";

const emptyCredentials = { token: "", caPem: "", clientCertificatePem: "", clientKeyPem: "" };
export function NvkeSiteScreen(props: NvkeSiteScreenProps) {
  const {
    site,
    loading,
    error,
    access,
    pending,
    operations,
    onRefresh,
    onEnable,
    onRemove,
    onConfigure,
  } = props;
  const t = useTranslations("nvke");
  const [editing, setEditing] = useState<NvkeSurface | null>(null);
  const [confirm, setConfirm] = useState<"disable" | "remove" | "clear" | null>(null);
  const form = useForm<NvkeConnectionValues>({
    resolver: zodResolver(connectionSchema),
    defaultValues: {
      surface: "CONTROLLER",
      endpoint: "",
      timeoutSeconds: 30,
      allowHttp: false,
      credentialMode: "preserve",
      ...emptyCredentials,
    },
  });
  const credentialMode = useWatch({ control: form.control, name: "credentialMode" });
  const allowHttp = useWatch({ control: form.control, name: "allowHttp" });
  const connectionTable = fixedNvkeTable(
    nvkeSurfaces.map((surface) => ({
      surface,
      connection: site?.connections.find((c) => c.surface === surface) ?? null,
    }))
  );
  const connectionList = useCursorTableList(connectionTable, {
    id: "nvke.connections",
    label: t("interfaces"),
    searchPlaceholder: "",
    searchable: false,
  });
  const operationList = useCursorTableList(operations, {
    id: "nvke.operations",
    label: t("operations"),
    searchPlaceholder: "",
    searchable: false,
  });
  const mayConfigure = access.admin && !access.loading && !pending && !error;
  const openConnection = (surface: NvkeSurface) => {
    if (!mayConfigure) return;
    const connection = site?.connections.find((c) => c.surface === surface);
    form.reset({
      surface,
      endpoint: connection?.endpoint ?? "",
      timeoutSeconds: connection?.timeoutSeconds ?? 30,
      allowHttp: connection?.allowHttp ?? false,
      credentialMode: "preserve",
      ...emptyCredentials,
    });
    setEditing(surface);
  };
  const save = async () => {
    if (!mayConfigure) return false;
    if (await onConfigure(form.getValues())) {
      setEditing(null);
      form.reset();
      return true;
    }
    return false;
  };
  const connections = nvkeSurfaces.map((surface) => ({
    surface,
    connection: site?.connections.find((c) => c.surface === surface) ?? null,
  }));
  const connectionColumns: Column<(typeof connections)[number]>[] = [
    {
      id: "surface",
      header: t("interfaces"),
      cell: (row) => <code>{row.surface.toLowerCase()}</code>,
    },
    {
      id: "endpoint",
      header: t("endpoint"),
      cellClassName: "max-w-96",
      cell: (row) => (
        <span className="block truncate font-mono text-xs" title={row.connection?.endpoint}>
          {row.connection?.endpoint ?? t("notConfigured")}
        </span>
      ),
    },
    {
      id: "credentials",
      header: t("credentials"),
      cell: (row) => (row.connection?.hasCredentials ? t("stored") : t("none")),
    },
    {
      id: "verification",
      header: t("verification"),
      cell: (row) => (
        <Badge variant="outline">
          {row.connection?.verifiedAt ? t("verified") : t("notVerified")}
        </Badge>
      ),
    },
  ];
  const operationColumns: Column<NvkeOperationView>[] = [
    {
      id: "key",
      header: t("action"),
      cellClassName: "max-w-72",
      cell: (row) => <span className="block truncate font-mono text-xs">{row.operationKey}</span>,
    },
    {
      id: "status",
      header: t("state"),
      cell: (row) => (
        <Badge
          variant={
            row.status === "FAILED" || row.status === "UNCERTAIN" ? "destructive" : "secondary"
          }
        >
          {t(`status.${row.status}`)}
        </Badge>
      ),
    },
    {
      id: "stage",
      header: t("stage"),
      cellClassName: "max-w-52",
      cell: (row) => <code className="block truncate text-xs">{row.stage || "—"}</code>,
    },
    {
      id: "request",
      header: t("requestId"),
      cellClassName: "max-w-60",
      cell: (row) => <code className="block truncate text-xs">{row.requestId}</code>,
    },
  ];
  if (loading && !site)
    return (
      <PageShell title={t("title")}>
        <Skeleton className="h-64" />
      </PageShell>
    );
  if (!site)
    return (
      <PageShell title={t("title")}>
        <p role="alert">{error ?? t("siteUnavailable")}</p>
        <Button variant="outline" onClick={onRefresh}>
          {t("refresh")}
        </Button>
      </PageShell>
    );
  return (
    <PageShell
      title={site.name}
      description={
        <Link href="/nvke" className="hover:underline">
          {t("title")}
        </Link>
      }
      actions={
        <>
          <Button variant="outline" asChild>
            <Link href={`/nvke/${site.id}/resources`}>{t("resourcesTitle")}</Link>
          </Button>
          <Button variant="outline" disabled={pending} onClick={onRefresh}>
            <RefreshCwIcon className="size-4" />
            {t("refresh")}
          </Button>
          {access.admin && (
            <>
              <Button
                variant="outline"
                disabled={!mayConfigure}
                onClick={async () => {
                  if (site.isActive) setConfirm("disable");
                  else await onEnable(true);
                }}
              >
                {t(site.isActive ? "disableSite" : "enableSite")}
              </Button>
              <Button
                variant="destructive"
                disabled={!mayConfigure}
                onClick={() => setConfirm("remove")}
              >
                {t("removeSite")}
              </Button>
            </>
          )}
        </>
      }
    >
      {error && (
        <p role="alert" className="text-danger-fg text-sm">
          {error}
        </p>
      )}
      <DefinitionList
        items={[
          { term: t("namespace"), description: <code>{site.organizationNamespace}</code> },
          { term: t("state"), description: t(site.isActive ? "enabled" : "disabled") },
          { term: t("version"), description: site.version },
          {
            term: t("revision"),
            description: (
              <code className="text-xs [overflow-wrap:anywhere]">{site.upstreamRevision}</code>
            ),
          },
        ]}
      />
      <Section title={t("interfaces")} description={t("connectionsHelp")}>
        <ListPage
          embedded
          bounded
          list={connectionList}
          rows={connections}
          label={t("interfaces")}
          columns={connectionColumns}
          getRowId={(row) => row.surface}
          rowActions={
            access.admin
              ? (row) => (
                  <DropdownMenuItem
                    disabled={!mayConfigure}
                    onSelect={() => openConnection(row.surface)}
                  >
                    {t("configure")} · {row.surface.toLowerCase()}
                  </DropdownMenuItem>
                )
              : undefined
          }
          empty={{ icon: <CableIcon />, title: t("notConfigured") }}
        />
      </Section>
      {props.operatorAgent && (
        <NvkeOperatorAgentPanel
          {...props.operatorAgent}
          canManage={
            props.operatorAgent.canManage &&
            access.admin &&
            !access.loading &&
            !error &&
            (!pending || props.operatorAgent.pending) &&
            !props.temporal?.pending &&
            !props.metrics?.pending &&
            !props.operatorWorkbench?.pending &&
            !props.providerRequest?.pending
          }
        />
      )}
      {props.temporal && (
        <NvkeTemporalPanel
          {...props.temporal}
          canManage={
            props.temporal.canManage &&
            !props.operatorWorkbench?.pending &&
            !props.operatorAgent?.pending &&
            !props.metrics?.pending &&
            !props.providerRequest?.pending
          }
        />
      )}
      {props.metrics && (
        <NvkeMetricsPanel
          {...props.metrics}
          canManage={
            props.metrics.canManage &&
            !props.operatorWorkbench?.pending &&
            !props.operatorAgent?.pending &&
            !props.providerRequest?.pending
          }
        />
      )}
      {props.providerRequest && (
        <NvkeProviderRequestPanel
          {...props.providerRequest}
          canManage={props.providerRequest.canManage && !props.operatorWorkbench?.pending}
        />
      )}
      {props.operatorWorkbench && (
        <NvkeOperatorWorkbench
          {...props.operatorWorkbench}
          canManage={
            props.operatorWorkbench.canManage &&
            access.admin &&
            !access.loading &&
            !error &&
            (!pending || props.operatorWorkbench.pending) &&
            !props.operatorAgent?.pending &&
            !props.temporal?.pending &&
            !props.metrics?.pending &&
            !props.providerRequest?.pending
          }
        />
      )}
      <NvkeNativePanel
        key={`${site.id}:${props.contractRevision}:${props.actions.length}:${props.recovery?.requestId ?? "new"}`}
        {...props}
        disabled={props.recovery?.operationKey.startsWith("tooling.")}
        access={{ ...access, loading: access.loading || Boolean(error) }}
      />
      {props.workloadAccess && <NvkeWorkloadAccessPanel {...props.workloadAccess} />}
      <Section title={t("operations")} description={t("operationsHelp")}>
        <ListPage
          embedded
          list={operationList}
          rows={operations.rows}
          label={t("operations")}
          loading={operations.state === "loading"}
          stale={operations.isStale}
          error={operations.state === "error" ? { message: t("readFailed") } : null}
          onRetry={operations.retry}
          nextCursor={operations.nextCursor}
          totalCount={operations.totalCount}
          columns={operationColumns}
          getRowId={(row) => row.id}
          rowHref={(row) => `/nvke/${site.id}/operations/${row.id}`}
          empty={{
            icon: <ClockIcon />,
            title: t("noOperations"),
            description: t("noOperationsHelp"),
          }}
        />
      </Section>
      <Sheet
        open={Boolean(editing)}
        onOpenChange={(open) => {
          if (!pending && !open) {
            setEditing(null);
            form.reset();
          }
        }}
      >
        <SheetContent>
          <SheetHeader>
            <SheetTitle>
              {t("configure")} · {editing?.toLowerCase()}
            </SheetTitle>
            <SheetDescription>{t("credentialHelp")}</SheetDescription>
          </SheetHeader>
          <form
            className="flex min-h-0 flex-col gap-4 overflow-y-auto p-4"
            onSubmit={form.handleSubmit(async () => {
              if (!mayConfigure) return;
              if (form.getValues("credentialMode") === "clear") setConfirm("clear");
              else await save();
            })}
          >
            <div className="space-y-2">
              <Label htmlFor="nvke-endpoint">{t("endpoint")}</Label>
              <Input
                id="nvke-endpoint"
                autoComplete="off"
                disabled={pending}
                {...form.register("endpoint")}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="nvke-connection-timeout">{t("connectionTimeout")}</Label>
              <Input
                id="nvke-connection-timeout"
                type="number"
                min={1}
                max={300}
                disabled={pending}
                {...form.register("timeoutSeconds", { valueAsNumber: true })}
              />
            </div>
            <div className="flex items-center gap-2">
              <Checkbox
                id="nvke-http"
                disabled={pending}
                checked={allowHttp}
                onCheckedChange={(checked) => form.setValue("allowHttp", checked === true)}
              />
              <Label htmlFor="nvke-http">{t("allowHttp")}</Label>
            </div>
            <div className="space-y-2">
              <Label htmlFor="nvke-credential-mode">{t("credentials")}</Label>
              <Select
                value={credentialMode}
                disabled={pending}
                onValueChange={(mode) => {
                  if (mode === "preserve" || mode === "replace" || mode === "clear") {
                    form.setValue("credentialMode", mode);
                    for (const key of Object.keys(
                      emptyCredentials
                    ) as (keyof typeof emptyCredentials)[])
                      form.setValue(key, "");
                  }
                }}
              >
                <SelectTrigger id="nvke-credential-mode" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(["preserve", "replace", "clear"] as const).map((mode) => (
                    <SelectItem key={mode} value={mode}>
                      {t(mode)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {credentialMode === "replace" && (
              <>
                <div className="space-y-2">
                  <Label htmlFor="nvke-token">{t("bearer")}</Label>
                  <Input
                    id="nvke-token"
                    type="password"
                    autoComplete="new-password"
                    disabled={pending}
                    {...form.register("token")}
                  />
                </div>
                {(["caPem", "clientCertificatePem", "clientKeyPem"] as const).map((name) => (
                  <div className="space-y-2" key={name}>
                    <Label htmlFor={`nvke-${name}`}>{t(name)}</Label>
                    <Textarea
                      id={`nvke-${name}`}
                      className="min-h-24 font-mono text-xs"
                      autoComplete="off"
                      spellCheck={false}
                      disabled={pending}
                      {...form.register(name)}
                    />
                  </div>
                ))}
              </>
            )}
            {Object.keys(form.formState.errors).length > 0 && (
              <p role="alert" className="text-danger-fg text-sm">
                {t("invalidForm")}
              </p>
            )}
            <SheetFooter>
              <Button type="submit" disabled={!mayConfigure}>
                {t(pending ? "working" : "save")}
              </Button>
            </SheetFooter>
          </form>
        </SheetContent>
      </Sheet>
      <ConfirmDialog
        open={Boolean(confirm)}
        onOpenChange={(open) => {
          if (!open) setConfirm(null);
        }}
        title={t(
          confirm === "remove" ? "removeSite" : confirm === "clear" ? "clear" : "disableSite"
        )}
        description={t(
          confirm === "remove" ? "removeHelp" : confirm === "clear" ? "clearHelp" : "disableHelp",
          { site: site.name }
        )}
        destructive
        confirmDisabled={!mayConfigure}
        onConfirm={() =>
          confirm === "remove" ? onRemove() : confirm === "clear" ? save() : onEnable(false)
        }
      />
    </PageShell>
  );
}
