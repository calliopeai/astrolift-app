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
import { temporalAuthoritySchema } from "./schema";
import type { NvkeSiteView } from "./types";

export type NvkeTemporalValues = z.infer<typeof temporalAuthoritySchema>;
export type NvkeTemporalAction = "verify" | "enable" | "disable" | "remove";
export interface NvkeTemporalAuthority {
  id: string;
  version: number;
  siteId: string;
  siteVersion: number;
  address: string;
  namespace: string;
  taskQueue: string;
  serverName: string;
  useTls: boolean;
  isEnabled: boolean;
  timeoutSeconds: number;
  hasCredentials: boolean;
  verifiedAt: string | null;
  verificationCode: string;
  deletedAt: string | null;
}
export interface NvkeTemporalPanelProps {
  site: Pick<NvkeSiteView, "id" | "name" | "version" | "isActive">;
  authority: NvkeTemporalAuthority | null;
  loading: boolean;
  error: string | null;
  canManage: boolean;
  pending: boolean;
  onRefresh: () => void;
  onConfigure: (values: NvkeTemporalValues) => Promise<boolean>;
  onAction: (action: NvkeTemporalAction) => Promise<boolean>;
}
const emptyCredentials = { apiKey: "", caPem: "", clientCertificatePem: "", clientKeyPem: "" };
export function NvkeTemporalPanel(props: NvkeTemporalPanelProps) {
  return (
    <TemporalEditor
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
function TemporalEditor(props: NvkeTemporalPanelProps) {
  const t = useTranslations("nvke");
  const [editing, setEditing] = useState(false);
  const [review, setReview] = useState<NvkeTemporalAction | "configure" | null>(null);
  const form = useForm<NvkeTemporalValues>({
    resolver: zodResolver(temporalAuthoritySchema),
    defaultValues: {
      address: props.authority?.address ?? "",
      namespace: props.authority?.namespace ?? "",
      taskQueue: props.authority?.taskQueue ?? "",
      serverName: props.authority?.serverName ?? "",
      useTls: props.authority?.useTls ?? true,
      timeoutSeconds: props.authority?.timeoutSeconds ?? 30,
      credentialMode: "preserve",
      ...emptyCredentials,
    },
  });
  const mode = useWatch({ control: form.control, name: "credentialMode" });
  const useTls = useWatch({ control: form.control, name: "useTls" });
  const mayWrite = props.canManage && !props.loading && !props.pending && !props.error;
  const labels = {
    verify: "temporalVerify",
    enable: "temporalEnable",
    disable: "temporalDisable",
    remove: "temporalRemove",
    configure: "configure",
  } as const;
  const target = review === "configure" ? form.getValues() : props.authority;
  return (
    <Section title={t("temporalTitle")} description={t("temporalHelp")}>
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
                  {t("temporalVerify")}
                </Button>
                <Button
                  variant="outline"
                  disabled={!mayWrite}
                  onClick={() => setReview(props.authority!.isEnabled ? "disable" : "enable")}
                >
                  {t(props.authority.isEnabled ? "temporalDisable" : "temporalEnable")}
                </Button>
                <Button
                  variant="destructive"
                  disabled={!mayWrite}
                  onClick={() => setReview("remove")}
                >
                  {t("temporalRemove")}
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
              term: t("temporalAddress"),
              description: (
                <code className="text-xs [overflow-wrap:anywhere]">{props.authority.address}</code>
              ),
            },
            {
              term: t("temporalNamespace"),
              description: (
                <code className="text-xs [overflow-wrap:anywhere]">
                  {props.authority.namespace}
                </code>
              ),
            },
            {
              term: t("temporalQueue"),
              description: (
                <code className="text-xs [overflow-wrap:anywhere]">
                  {props.authority.taskQueue}
                </code>
              ),
            },
            { term: t("temporalServerName"), description: props.authority.serverName || "—" },
            {
              term: t("temporalTls"),
              description: t(props.authority.useTls ? "enabled" : "disabled"),
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
                props.authority.verificationCode === "NAMESPACE_VERIFIED" &&
                props.authority.verifiedAt
                  ? `${t("temporalVerified")} · ${props.authority.verifiedAt}`
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
            <SheetTitle>{t("temporalTitle")}</SheetTitle>
            <SheetDescription>{t("credentialHelp")}</SheetDescription>
          </SheetHeader>
          <form
            noValidate
            className="flex min-h-0 flex-col gap-4 overflow-y-auto p-4"
            onSubmit={form.handleSubmit(() => {
              if (mayWrite) setReview("configure");
            })}
          >
            {(
              [
                ["address", "temporalAddress"],
                ["namespace", "temporalNamespace"],
                ["taskQueue", "temporalQueue"],
                ["serverName", "temporalServerName"],
              ] as const
            ).map(([name, label]) => (
              <div key={name} className="space-y-2">
                <Label htmlFor={`nvke-temporal-${name}`}>{t(label)}</Label>
                <Input
                  id={`nvke-temporal-${name}`}
                  autoComplete="off"
                  disabled={!mayWrite}
                  {...form.register(name)}
                />
              </div>
            ))}
            <div className="space-y-2">
              <Label htmlFor="nvke-temporal-timeout">{t("connectionTimeout")}</Label>
              <Input
                id="nvke-temporal-timeout"
                type="number"
                min={1}
                max={300}
                disabled={!mayWrite}
                {...form.register("timeoutSeconds", { valueAsNumber: true })}
              />
            </div>
            <div className="flex items-center gap-2">
              <Checkbox
                id="nvke-temporal-tls"
                checked={useTls}
                disabled={!mayWrite}
                onCheckedChange={(value) => form.setValue("useTls", value === true)}
              />
              <Label htmlFor="nvke-temporal-tls">{t("temporalTls")}</Label>
            </div>
            <div className="space-y-2">
              <Label htmlFor="nvke-temporal-mode">{t("credentials")}</Label>
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
                <SelectTrigger id="nvke-temporal-mode" className="w-full">
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
                  <Label htmlFor="nvke-temporal-api-key">{t("temporalApiKey")}</Label>
                  <Input
                    id="nvke-temporal-api-key"
                    type="password"
                    autoComplete="new-password"
                    disabled={!mayWrite}
                    {...form.register("apiKey")}
                  />
                </div>
                {(["caPem", "clientCertificatePem", "clientKeyPem"] as const).map((name) => (
                  <div key={name} className="space-y-2">
                    <Label htmlFor={`nvke-temporal-${name}`}>{t(name)}</Label>
                    <Textarea
                      id={`nvke-temporal-${name}`}
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
            {t("temporalReview", {
              site: props.site.name,
              siteVersion: props.site.version,
              version: props.authority?.version ?? 0,
              address: target?.address ?? "—",
              namespace: target?.namespace ?? "—",
              queue: target?.taskQueue ?? "—",
            })}
            {review === "configure" && (
              <span className="mt-2 block">
                {t("credentials")}: {t(mode)} · {t("temporalTls")}:{" "}
                {t(useTls ? "enabled" : "disabled")}
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
