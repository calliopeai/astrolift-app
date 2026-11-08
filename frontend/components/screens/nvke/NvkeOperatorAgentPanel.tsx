"use client";

import { useRef, useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { useTranslations } from "next-intl";
import type { z } from "zod";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";
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
import { operatorAgentAuthoritySchema } from "./schema";
import type { NvkeSiteView } from "./types";

export type NvkeOperatorAgentValues = z.infer<typeof operatorAgentAuthoritySchema>;
export type NvkeOperatorAgentAction = "verify" | "enable" | "disable" | "remove";
export interface NvkeOperatorAgentAuthority {
  id: string;
  version: number;
  siteId: string;
  siteVersion: number;
  endpoint: string;
  instanceId: string;
  bundleManifestSha256: string;
  isEnabled: boolean;
  timeoutSeconds: number;
  hasCredentials: boolean;
  verifiedAt: string | null;
  verificationCode: string;
  deletedAt: string | null;
}
export interface NvkeOperatorAgentPanelProps {
  site: Pick<NvkeSiteView, "id" | "name" | "version" | "isActive">;
  authority: NvkeOperatorAgentAuthority | null;
  loading: boolean;
  error: string | null;
  canManage: boolean;
  pending: boolean;
  onRefresh: () => void;
  onConfigure: (values: NvkeOperatorAgentValues) => Promise<boolean>;
  onAction: (action: NvkeOperatorAgentAction) => Promise<boolean>;
}
const emptyCredentials = { caPem: "", clientCertificatePem: "", clientKeyPem: "" };
const fields = [
  ["endpoint", "endpoint"],
  ["instanceId", "agentInstance"],
  ["bundleManifestSha256", "agentBundle"],
] as const;

export function NvkeOperatorAgentPanel(props: NvkeOperatorAgentPanelProps) {
  return (
    <AgentEditor
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
function AgentEditor(props: NvkeOperatorAgentPanelProps) {
  const t = useTranslations("nvke");
  const [editing, setEditing] = useState(false);
  const reviewedValues = useRef<NvkeOperatorAgentValues | null>(null);
  const [reviewTarget, setReviewTarget] = useState<Pick<
    NvkeOperatorAgentValues,
    "endpoint" | "instanceId" | "bundleManifestSha256" | "timeoutSeconds" | "credentialMode"
  > | null>(null);
  const [unconfirmed, setUnconfirmed] = useState(false);
  const [review, setReview] = useState<NvkeOperatorAgentAction | "configure" | null>(null);
  const form = useForm<NvkeOperatorAgentValues>({
    resolver: zodResolver(operatorAgentAuthoritySchema),
    defaultValues: {
      endpoint: props.authority?.endpoint ?? "",
      instanceId: props.authority?.instanceId ?? "",
      bundleManifestSha256: props.authority?.bundleManifestSha256 ?? "",
      timeoutSeconds: props.authority?.timeoutSeconds ?? 30,
      credentialMode: props.authority ? "preserve" : "replace",
      ...emptyCredentials,
    },
  });
  const mode = useWatch({ control: form.control, name: "credentialMode" });
  const mayWrite =
    props.canManage && !props.loading && !props.pending && !props.error && !unconfirmed;
  const labels = {
    verify: "agentVerify",
    enable: "agentEnable",
    disable: "agentDisable",
    remove: "agentRemove",
    configure: "configure",
  } as const;
  const target = review === "configure" ? reviewTarget : props.authority;
  return (
    <Section title={t("agentTitle")} description={t("agentHelp")}>
      <div className="flex flex-wrap gap-2">
        <Button
          variant="outline"
          disabled={props.pending}
          onClick={() => {
            setUnconfirmed(false);
            props.onRefresh();
          }}
        >
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
                  {t("agentVerify")}
                </Button>
                <Button
                  variant="outline"
                  disabled={!mayWrite}
                  onClick={() => setReview(props.authority!.isEnabled ? "disable" : "enable")}
                >
                  {t(props.authority.isEnabled ? "agentDisable" : "agentEnable")}
                </Button>
                <Button
                  variant="destructive"
                  disabled={!mayWrite}
                  onClick={() => setReview("remove")}
                >
                  {t("agentRemove")}
                </Button>
              </>
            )}
          </>
        )}
      </div>
      {unconfirmed && !props.error && (
        <p role="alert" className="text-danger-fg text-sm">
          {t("configurationFailed")}
        </p>
      )}
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
            ...fields.map(([name, label]) => ({
              term: t(label),
              description: (
                <code className="text-xs [overflow-wrap:anywhere]">{props.authority![name]}</code>
              ),
            })),
            { term: t("connectionTimeout"), description: props.authority.timeoutSeconds },
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
                props.authority.verificationCode === "AGENT_VERIFIED" && props.authority.verifiedAt
                  ? `${t("agentVerified")} · ${props.authority.verifiedAt}`
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
            reviewedValues.current = null;
            form.reset();
          }
        }}
      >
        <SheetContent>
          <SheetHeader>
            <SheetTitle>{t("agentTitle")}</SheetTitle>
            <SheetDescription>{t("agentCredentialHelp")}</SheetDescription>
          </SheetHeader>
          <form
            noValidate
            className="flex min-h-0 flex-col gap-4 overflow-y-auto p-4"
            onSubmit={(event) => {
              void form.handleSubmit((values) => {
                if (!mayWrite) return;
                reviewedValues.current = { ...values };
                setReviewTarget({
                  endpoint: values.endpoint,
                  instanceId: values.instanceId,
                  bundleManifestSha256: values.bundleManifestSha256,
                  timeoutSeconds: values.timeoutSeconds,
                  credentialMode: values.credentialMode,
                });
                for (const name of Object.keys(
                  emptyCredentials
                ) as (keyof typeof emptyCredentials)[])
                  form.setValue(name, "");
                setReview("configure");
              })(event);
            }}
          >
            {fields.map(([name, label]) => (
              <div key={name} className="space-y-2">
                <Label htmlFor={`nvke-agent-${name}`}>{t(label)}</Label>
                <Input
                  id={`nvke-agent-${name}`}
                  autoComplete="off"
                  spellCheck={false}
                  disabled={!mayWrite}
                  {...form.register(name)}
                />
              </div>
            ))}
            <div className="space-y-2">
              <Label htmlFor="nvke-agent-timeout">{t("connectionTimeout")}</Label>
              <Input
                id="nvke-agent-timeout"
                type="number"
                min={1}
                max={300}
                disabled={!mayWrite}
                {...form.register("timeoutSeconds", { valueAsNumber: true })}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="nvke-agent-mode">{t("credentials")}</Label>
              <Select
                value={mode}
                disabled={!mayWrite}
                onValueChange={(value) => {
                  if (value === "preserve" || value === "replace") {
                    form.setValue("credentialMode", value);
                    for (const name of Object.keys(
                      emptyCredentials
                    ) as (keyof typeof emptyCredentials)[])
                      form.setValue(name, "");
                  }
                }}
              >
                <SelectTrigger id="nvke-agent-mode" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(props.authority
                    ? (["preserve", "replace"] as const)
                    : (["replace"] as const)
                  ).map((value) => (
                    <SelectItem key={value} value={value}>
                      {t(value)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {mode === "replace" &&
              (Object.keys(emptyCredentials) as (keyof typeof emptyCredentials)[]).map((name) => (
                <div key={name} className="space-y-2">
                  <Label htmlFor={`nvke-agent-${name}`}>{t(name)}</Label>
                  <Textarea
                    id={`nvke-agent-${name}`}
                    autoComplete="off"
                    spellCheck={false}
                    className="min-h-24 font-mono text-xs"
                    disabled={!mayWrite}
                    {...form.register(name)}
                  />
                </div>
              ))}
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
          if (!open) {
            reviewedValues.current = null;
            setReview(null);
          }
        }}
        title={t(labels[review ?? "configure"])}
        description={
          <>
            {t("agentReview", {
              site: props.site.name,
              siteVersion: props.site.version,
              version: props.authority?.version ?? 0,
              endpoint: target?.endpoint ?? "—",
              instance: target?.instanceId ?? "—",
              bundle: target?.bundleManifestSha256 ?? "—",
            })}
            {review !== "verify" && <span className="mt-2 block">{t("agentAuthorityEffect")}</span>}
            {review === "configure" && (
              <span className="mt-2 block">
                {t("credentials")}: {t(reviewTarget?.credentialMode ?? mode)} ·{" "}
                {t("connectionTimeout")}: {reviewTarget?.timeoutSeconds}
              </span>
            )}
          </>
        }
        destructive={review === "remove" || review === "disable"}
        confirmDisabled={!mayWrite}
        onConfirm={async () => {
          if (!mayWrite || !review) return false;
          if (review !== "configure") {
            try {
              const ok = await props.onAction(review);
              if (!ok) {
                setUnconfirmed(true);
                setReview(null);
              }
              return ok;
            } catch {
              setUnconfirmed(true);
              setReview(null);
              return false;
            }
          }
          const values = reviewedValues.current;
          reviewedValues.current = null;
          if (!values) {
            setReview(null);
            return false;
          }
          for (const name of Object.keys(emptyCredentials) as (keyof typeof emptyCredentials)[])
            form.setValue(name, "");
          try {
            const ok = await props.onConfigure(values);
            if (!ok) setUnconfirmed(true);
            return ok;
          } catch {
            setUnconfirmed(true);
            return false;
          } finally {
            reviewedValues.current = null;
            form.reset();
            setEditing(false);
            setReview(null);
          }
        }}
      />
    </Section>
  );
}
