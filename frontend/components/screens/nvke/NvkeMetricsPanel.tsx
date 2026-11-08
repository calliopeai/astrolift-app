"use client";

import { useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { useTranslations } from "next-intl";
import type { z } from "zod";
import { ConfirmDialog } from "@/components/ConfirmDialog";
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
import { metricsAuthoritySchema } from "./schema";
import type { NvkeSiteView } from "./types";

export type NvkeMetricsValues = z.infer<typeof metricsAuthoritySchema>;
export type NvkeMetricsAction = "verify" | "enable" | "disable" | "remove";
export interface NvkeMetricsAuthority {
  id: string;
  version: number;
  siteId: string;
  siteVersion: number;
  endpoint: string;
  allowHttp: boolean;
  isEnabled: boolean;
  timeoutSeconds: number;
  hasCredentials: boolean;
  verifiedAt: string | null;
  verificationCode: string;
  deletedAt: string | null;
}
export interface NvkeMetricsPanelProps {
  site: Pick<NvkeSiteView, "id" | "name" | "version" | "isActive">;
  authority: NvkeMetricsAuthority | null;
  loading: boolean;
  error: string | null;
  canManage: boolean;
  pending: boolean;
  onRefresh: () => void;
  onConfigure: (values: NvkeMetricsValues) => Promise<boolean>;
  onAction: (action: NvkeMetricsAction) => Promise<boolean>;
}
const emptyCredentials = { token: "", caPem: "", clientCertificatePem: "", clientKeyPem: "" };
export function NvkeMetricsPanel(props: NvkeMetricsPanelProps) {
  return (
    <MetricsEditor
      key={JSON.stringify([
        props.site.id,
        props.site.version,
        props.authority?.id,
        props.authority?.version,
        props.canManage,
        props.loading,
      ])}
      {...props}
    />
  );
}
function MetricsEditor(props: NvkeMetricsPanelProps) {
  const t = useTranslations("nvke");
  const [editing, setEditing] = useState(false);
  const [review, setReview] = useState<NvkeMetricsAction | "configure" | null>(null);
  const form = useForm<NvkeMetricsValues>({
    resolver: zodResolver(metricsAuthoritySchema),
    defaultValues: {
      endpoint: props.authority?.endpoint ?? "",
      allowHttp: props.authority?.allowHttp ?? false,
      timeoutSeconds: props.authority?.timeoutSeconds ?? 30,
      credentialMode: "preserve",
      ...emptyCredentials,
    },
  });
  const mode = useWatch({ control: form.control, name: "credentialMode" });
  const allowHttp = useWatch({ control: form.control, name: "allowHttp" });
  const mayWrite = props.canManage && !props.loading && !props.pending && !props.error;
  const labels = {
    verify: "metricsVerify",
    enable: "metricsEnable",
    disable: "metricsDisable",
    remove: "metricsRemove",
    configure: "configure",
  } as const;
  const target = review === "configure" ? form.getValues() : props.authority;
  return (
    <Section title={t("metricsTitle")} description={t("metricsHelp")}>
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" disabled={props.pending} onClick={props.onRefresh}>
          {t("refresh")}
        </Button>
        {props.canManage && (
          <>
            <Button variant="outline" disabled={!mayWrite} onClick={() => setEditing(true)}>
              {t("configure")}
            </Button>
            {props.authority && (
              <>
                <Button
                  variant="outline"
                  disabled={!mayWrite || !props.site.isActive || !props.authority.isEnabled}
                  onClick={() => setReview("verify")}
                >
                  {t("metricsVerify")}
                </Button>
                <Button
                  variant="outline"
                  disabled={!mayWrite}
                  onClick={() => setReview(props.authority!.isEnabled ? "disable" : "enable")}
                >
                  {t(props.authority.isEnabled ? "metricsDisable" : "metricsEnable")}
                </Button>
                <Button
                  variant="destructive"
                  disabled={!mayWrite}
                  onClick={() => setReview("remove")}
                >
                  {t("metricsRemove")}
                </Button>
              </>
            )}
          </>
        )}
      </div>
      {props.loading ? (
        <Skeleton className="h-32" />
      ) : props.error ? (
        <p role="alert" className="text-danger-fg text-sm">
          {props.error}
        </p>
      ) : !props.authority ? (
        <p>{t("notConfigured")}</p>
      ) : (
        <DefinitionList
          items={[
            {
              term: t("endpoint"),
              description: (
                <code className="text-xs [overflow-wrap:anywhere]">{props.authority.endpoint}</code>
              ),
            },
            {
              term: t("allowHttp"),
              description: t(props.authority.allowHttp ? "enabled" : "disabled"),
            },
            {
              term: t("state"),
              description: t(props.authority.isEnabled ? "enabled" : "disabled"),
            },
            {
              term: t("credentials"),
              description: t(props.authority.hasCredentials ? "stored" : "none"),
            },
            { term: t("version"), description: props.authority.version },
            {
              term: t("verification"),
              description:
                props.authority.verificationCode === "METRICS_READ" && props.authority.verifiedAt
                  ? `${t("metricsVerified")} · ${props.authority.verifiedAt}`
                  : t("notVerified"),
            },
          ]}
        />
      )}
      <Sheet
        open={editing}
        onOpenChange={(open) => {
          if (!props.pending && !open) {
            setEditing(false);
            form.reset();
          }
        }}
      >
        <SheetContent>
          <SheetHeader>
            <SheetTitle>{t("metricsTitle")}</SheetTitle>
            <SheetDescription>{t("credentialHelp")}</SheetDescription>
          </SheetHeader>
          <form
            noValidate
            className="flex min-h-0 flex-col gap-4 overflow-y-auto p-4"
            onSubmit={form.handleSubmit(() => {
              if (mayWrite) setReview("configure");
            })}
          >
            <div className="space-y-2">
              <Label htmlFor="nvke-metrics-endpoint">{t("endpoint")}</Label>
              <Input
                id="nvke-metrics-endpoint"
                autoComplete="off"
                disabled={!mayWrite}
                {...form.register("endpoint")}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="nvke-metrics-timeout">{t("connectionTimeout")}</Label>
              <Input
                id="nvke-metrics-timeout"
                type="number"
                min={1}
                max={300}
                disabled={!mayWrite}
                {...form.register("timeoutSeconds", { valueAsNumber: true })}
              />
            </div>
            <div className="flex items-center gap-2">
              <Checkbox
                id="nvke-metrics-tls"
                checked={allowHttp}
                disabled={!mayWrite}
                onCheckedChange={(value) => form.setValue("allowHttp", value === true)}
              />
              <Label htmlFor="nvke-metrics-tls">{t("allowHttp")}</Label>
            </div>
            <div className="space-y-2">
              <Label htmlFor="nvke-metrics-mode">{t("credentials")}</Label>
              <Select
                value={mode}
                disabled={!mayWrite}
                onValueChange={(value) => {
                  if (value === "preserve" || value === "replace" || value === "clear") {
                    form.setValue("credentialMode", value);
                    for (const key of Object.keys(
                      emptyCredentials
                    ) as (keyof typeof emptyCredentials)[])
                      form.setValue(key, "");
                  }
                }}
              >
                <SelectTrigger id="nvke-metrics-mode" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(["preserve", "replace", "clear"] as const).map((value) => (
                    <SelectItem key={value} value={value}>
                      {t(value)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {mode === "replace" && (
              <>
                <div className="space-y-2">
                  <Label htmlFor="nvke-metrics-api-key">{t("metricsToken")}</Label>
                  <Input
                    id="nvke-metrics-api-key"
                    type="password"
                    autoComplete="new-password"
                    disabled={!mayWrite}
                    {...form.register("token")}
                  />
                </div>
                {(["caPem", "clientCertificatePem", "clientKeyPem"] as const).map((name) => (
                  <div key={name} className="space-y-2">
                    <Label htmlFor={`nvke-metrics-${name}`}>{t(name)}</Label>
                    <Textarea
                      id={`nvke-metrics-${name}`}
                      autoComplete="off"
                      spellCheck={false}
                      className="min-h-24 font-mono text-xs"
                      disabled={!mayWrite}
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
              <Button type="submit" disabled={!mayWrite}>
                {t("reviewRequest")}
              </Button>
            </SheetFooter>
          </form>
        </SheetContent>
      </Sheet>
      <ConfirmDialog
        open={review !== null}
        onOpenChange={(open) => {
          if (!open) setReview(null);
        }}
        title={t(labels[review ?? "configure"])}
        description={
          <>
            {t("metricsReview", {
              site: props.site.name,
              siteVersion: props.site.version,
              version: props.authority?.version ?? 0,
              endpoint: target?.endpoint ?? "—",
            })}
            {review === "configure" && (
              <span className="mt-2 block">
                {t("credentials")}: {t(mode)} · {t("allowHttp")}:{" "}
                {t(allowHttp ? "enabled" : "disabled")}
              </span>
            )}
          </>
        }
        destructive={
          review === "remove" ||
          review === "disable" ||
          (review === "configure" && mode === "clear")
        }
        confirmDisabled={!mayWrite}
        onConfirm={async () => {
          if (!mayWrite || !review) return false;
          if (review !== "configure") {
            const ok = await props.onAction(review);
            if (!ok) setReview(null);
            return ok;
          }
          const values = form.getValues();
          for (const key of Object.keys(emptyCredentials) as (keyof typeof emptyCredentials)[])
            form.setValue(key, "");
          const ok = await props.onConfigure(values);
          form.reset();
          setEditing(false);
          setReview(null);
          return ok;
        }}
      />
    </Section>
  );
}
