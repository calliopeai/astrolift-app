"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { PlayIcon, ShieldCheckIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { useForm, useWatch } from "react-hook-form";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
import { Textarea } from "@/components/ui/textarea";

import { nativeTarget } from "./native-target";
import { nativeRequestSchema } from "./schema";
import {
  mayExecute,
  type NvkeActionView,
  type NvkeRequestValues,
  type NvkeSiteScreenProps,
} from "./types";

export type NvkeNativePanelProps = { presentation?: "collection"; disabled?: boolean } & Pick<
  NvkeSiteScreenProps,
  | "site"
  | "actions"
  | "access"
  | "pending"
  | "catalogLoading"
  | "contractRevision"
  | "schemaText"
  | "templateText"
  | "recovery"
  | "onRequest"
>;

export function NvkeNativePanel({
  site,
  actions,
  access,
  pending,
  catalogLoading,
  contractRevision,
  schemaText,
  templateText,
  recovery,
  onRequest,
  presentation,
  disabled,
}: NvkeNativePanelProps) {
  const t = useTranslations("nvke");
  const first =
    actions.find((a) => a.key === recovery?.operationKey) ??
    actions.find((a) => a.key === "listClusters") ??
    actions[0];
  const form = useForm<NvkeRequestValues>({
    resolver: zodResolver(nativeRequestSchema),
    defaultValues: {
      operationKey: first?.key ?? "",
      parametersText: recovery ? "{}" : first ? templateText(first) : "{}",
      timeoutSeconds: recovery?.timeoutSeconds ?? 7200,
    },
  });
  const operationKey = useWatch({ control: form.control, name: "operationKey" });
  const selected = actions.find((action) => action.key === operationKey);
  const [review, setReview] = useState<{
    values: NvkeRequestValues;
    siteId: string;
    siteVersion: number;
    revision: string | null;
  } | null>(null);
  const [switchTo, setSwitchTo] = useState<NvkeActionView | null>(null);
  const ready = Boolean(
    site?.isActive &&
    selected &&
    mayExecute(access, selected.permission) &&
    site.connections.some((c) => c.surface === selected.surface && c.isEnabled) &&
    contractRevision === site.upstreamRevision &&
    !catalogLoading &&
    !disabled &&
    !pending
  );
  const reviewedSourceCurrent =
    !!review &&
    review.siteId === site?.id &&
    (review.siteVersion === site.version || review.siteVersion === recovery?.expectedSiteVersion) &&
    review.revision === contractRevision &&
    review.values.operationKey === selected?.key;
  const choose = (action: NvkeActionView) => {
    form.reset({
      operationKey: action.key,
      parametersText: templateText(action),
      timeoutSeconds: 7200,
    });
    setSwitchTo(null);
  };
  const request = async (values: NvkeRequestValues) => {
    if (!ready) return false;
    return onRequest(values);
  };
  return (
    <Section
      title={t(presentation === "collection" ? "collectionActions" : "nativeTitle")}
      description={t(presentation === "collection" ? "collectionActionsHelp" : "nativeHelp")}
    >
      <form
        className="flex min-w-0 flex-col gap-4"
        onSubmit={form.handleSubmit(async (values) => {
          if (!ready || !selected) return;
          if (selected.mutates && site)
            setReview({
              values: { ...values },
              siteId: site.id,
              siteVersion: recovery?.expectedSiteVersion ?? site.version,
              revision: contractRevision,
            });
          else await request(values);
        })}
      >
        <div className="grid min-w-0 gap-4 md:grid-cols-[minmax(0,1fr)_12rem]">
          <div className="min-w-0 space-y-2">
            <Label htmlFor="nvke-action">{t("action")}</Label>
            <Select
              value={selected?.key ?? ""}
              disabled={pending || catalogLoading || Boolean(recovery)}
              onValueChange={(key) => {
                const next = actions.find((a) => a.key === key);
                if (next) {
                  if (form.formState.isDirty) setSwitchTo(next);
                  else choose(next);
                }
              }}
            >
              <SelectTrigger id="nvke-action" className="w-full">
                <SelectValue placeholder={t("chooseAction")} />
              </SelectTrigger>
              <SelectContent>
                {actions.map((action) => (
                  <SelectItem key={action.key} value={action.key}>
                    <span className="font-mono">
                      {action.surface.toLowerCase()} · {action.key}
                    </span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="nvke-timeout">{t("timeout")}</Label>
            <Input
              id="nvke-timeout"
              type="number"
              min={30}
              max={86400}
              disabled={pending || Boolean(recovery)}
              {...form.register("timeoutSeconds", { valueAsNumber: true })}
            />
          </div>
        </div>
        {selected && (
          <div className="flex min-w-0 flex-wrap items-center gap-2 text-xs">
            <Badge variant="outline">{selected.surface}</Badge>
            <Badge variant="secondary">
              <ShieldCheckIcon className="mr-1 size-3" />
              {selected.permission}
            </Badge>
            <code className="min-w-0 [overflow-wrap:anywhere]">
              {selected.verb} {selected.path || selected.resourceKind}
            </code>
          </div>
        )}
        <div className="space-y-2">
          <Label htmlFor="nvke-parameters">{t("parameters")}</Label>
          <Textarea
            id="nvke-parameters"
            className="min-h-64 font-mono text-xs"
            autoComplete="off"
            autoCorrect="off"
            spellCheck={false}
            disabled={pending}
            aria-invalid={Boolean(form.formState.errors.parametersText)}
            {...form.register("parametersText")}
          />
          <p className="text-muted-foreground text-xs">{t("parametersHelp")}</p>
        </div>
        {Object.keys(form.formState.errors).length > 0 && (
          <p role="alert" className="text-danger-fg text-sm">
            {t("invalidForm")}
          </p>
        )}
        {recovery && (
          <p
            role="status"
            className="bg-warning-bg text-warning-fg rounded-lg p-3 text-sm [overflow-wrap:anywhere]"
          >
            {t("recoveryHelp", { id: recovery.requestId, version: recovery.expectedSiteVersion })}
          </p>
        )}
        {!ready && !pending && <p className="text-warning-fg text-sm">{t("cannotExecute")}</p>}
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" disabled={!ready}>
            <PlayIcon className="size-4" />
            {t(
              pending
                ? "working"
                : recovery
                  ? "retryRequest"
                  : selected?.mutates
                    ? "reviewRequest"
                    : "runRead"
            )}
          </Button>
          <span className="text-muted-foreground text-xs">{t("acceptedHelp")}</span>
        </div>
      </form>
      {selected && (
        <details className="min-w-0 text-sm">
          <summary className="cursor-pointer font-medium">{t("schema")}</summary>
          <pre className="bg-muted mt-3 max-h-80 overflow-auto rounded-lg p-3 font-mono text-xs [overflow-wrap:anywhere] whitespace-pre-wrap">
            {schemaText(selected)}
          </pre>
        </details>
      )}
      <ConfirmDialog
        open={Boolean(review)}
        onOpenChange={(open) => {
          if (!open) setReview(null);
        }}
        title={t("reviewRequest")}
        description={
          <>
            {t("mutationContext", {
              site: site?.name ?? "",
              action: selected?.key ?? "",
              version: review?.siteVersion ?? recovery?.expectedSiteVersion ?? site?.version ?? 0,
            })}
            <span className="mt-2 block font-mono text-xs [overflow-wrap:anywhere]">
              {t("mutationTarget", {
                target:
                  nativeTarget(review?.values.parametersText ?? "") ?? t("targetFromParameters"),
              })}
            </span>
          </>
        }
        confirmLabel={t("submitRequest")}
        destructive={selected?.verb === "DELETE" || selected?.verb === "delete"}
        confirmDisabled={!ready || !reviewedSourceCurrent}
        onConfirm={async () => (reviewedSourceCurrent && review ? request(review.values) : false)}
      />
      <ConfirmDialog
        open={Boolean(switchTo)}
        onOpenChange={(open) => {
          if (!open) setSwitchTo(null);
        }}
        title={t("discardTitle")}
        description={t("discardHelp")}
        onConfirm={() => {
          if (switchTo) choose(switchTo);
        }}
      />
    </Section>
  );
}
