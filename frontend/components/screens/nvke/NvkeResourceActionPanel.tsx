"use client";
import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { useTranslations } from "next-intl";
import { ConfirmDialog } from "@/components/ConfirmDialog";
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
import type { NvkeResourceSource } from "@/lib/nvke/recovery";
import { nativeRequestSchema } from "./schema";
import {
  mayExecute,
  type NvkeAccess,
  type NvkeActionView,
  type NvkeRecoveryView,
  type NvkeRequestValues,
} from "./types";

export interface NvkeResourceEditorView {
  id: string;
  siteVersion: number;
  operationKey: string;
  parametersText: string | null;
  preparationActionKey: string | null;
  concurrency: "native_version" | "observed_only";
  source: NvkeResourceSource;
}
export interface NvkeResourceActionPanelProps {
  siteName: string;
  siteVersion: number;
  target: string;
  actions: NvkeActionView[];
  selectedKey: string;
  access: NvkeAccess;
  sourceReady: boolean;
  pending: boolean;
  error: string | null;
  recovery: NvkeRecoveryView | null;
  editor: NvkeResourceEditorView | null;
  schemaText: string;
  onSelect: (key: string) => void;
  onLoad: () => Promise<boolean>;
  onRefresh: () => void;
  onPrepare: () => Promise<boolean>;
  onRecover: () => Promise<boolean>;
  onRequest: (values: NvkeRequestValues) => Promise<boolean>;
}

export function NvkeResourceActionPanel(props: NvkeResourceActionPanelProps) {
  const t = useTranslations("nvke");
  const [confirmLoad, setConfirmLoad] = useState(false);
  const [switchTo, setSwitchTo] = useState<string | null>(null);
  const selected = props.actions.find(
    (action) => action.key === props.selectedKey && action.mutates
  );
  const allowed =
    !!selected &&
    props.sourceReady &&
    mayExecute(props.access, selected.permission) &&
    !props.pending &&
    !props.error;
  const editor = props.editor?.operationKey === props.selectedKey ? props.editor : null;
  return (
    <Section title={t("editResource")} description={t("fixedTargetHelp")}>
      <p className="min-w-0 font-mono text-sm [overflow-wrap:anywhere]">{props.target}</p>
      {props.error && (
        <div className="space-y-2">
          <p role="alert" className="text-danger-fg text-sm">
            {props.error}
          </p>
          <Button variant="outline" disabled={props.pending} onClick={props.onRefresh}>
            {t("refresh")}
          </Button>
        </div>
      )}
      <div className="min-w-0 space-y-2">
        <Label htmlFor="nvke-resource-action">{t("action")}</Label>
        <Select
          value={props.selectedKey}
          disabled={props.pending || Boolean(props.recovery)}
          onValueChange={(key) => {
            if (!props.actions.some((action) => action.key === key && action.mutates)) return;
            if (editor) setSwitchTo(key);
            else props.onSelect(key);
          }}
        >
          <SelectTrigger id="nvke-resource-action" className="w-full">
            <SelectValue placeholder={t("chooseAction")} />
          </SelectTrigger>
          <SelectContent>
            {props.actions
              .filter((action) => action.mutates)
              .map((action) => (
                <SelectItem key={action.key} value={action.key}>
                  {action.key}
                </SelectItem>
              ))}
          </SelectContent>
        </Select>
      </div>
      <div className="flex flex-wrap gap-3">
        <Button variant="outline" disabled={!allowed} onClick={() => setConfirmLoad(true)}>
          {t("loadEdit")}
        </Button>
        {props.recovery && (
          <Button
            variant="outline"
            disabled={props.pending || !props.access.read}
            onClick={() => {
              void props.onRecover();
            }}
          >
            {t("recoverReceipt")}
          </Button>
        )}
      </div>
      {props.recovery && (
        <p role="status" className="text-warning-fg text-sm [overflow-wrap:anywhere]">
          {t("recoveryHelp", {
            id: props.recovery.requestId,
            version: props.recovery.expectedSiteVersion,
          })}
        </p>
      )}
      {editor?.preparationActionKey && (
        <div className="space-y-3">
          <p className="text-muted-foreground text-sm">{t("prepareEditHelp")}</p>
          <Button
            disabled={!allowed}
            onClick={() => {
              if (allowed) void props.onPrepare();
            }}
          >
            {t("prepareEdit")}
          </Button>
        </div>
      )}
      {editor?.parametersText !== null && editor && selected && (
        <ResourceDraft
          key={JSON.stringify([editor.id, editor.operationKey, editor.source])}
          {...props}
          editor={editor}
          action={selected}
          allowed={allowed && editor.siteVersion === props.siteVersion}
        />
      )}
      <ConfirmDialog
        open={confirmLoad}
        onOpenChange={setConfirmLoad}
        title={t("loadEdit")}
        description={t("loadEditHelp")}
        confirmDisabled={!allowed}
        confirmLabel={t("loadEdit")}
        onConfirm={async () => (allowed ? props.onLoad() : false)}
      />
      <ConfirmDialog
        open={switchTo !== null}
        onOpenChange={(open) => {
          if (!open) setSwitchTo(null);
        }}
        title={t("discardTitle")}
        description={t("discardHelp")}
        onConfirm={() => {
          if (switchTo) props.onSelect(switchTo);
          setSwitchTo(null);
        }}
      />
    </Section>
  );
}

function ResourceDraft(
  props: NvkeResourceActionPanelProps & {
    editor: NvkeResourceEditorView;
    action: NvkeActionView;
    allowed: boolean;
  }
) {
  const t = useTranslations("nvke");
  const form = useForm<NvkeRequestValues>({
    resolver: zodResolver(nativeRequestSchema),
    defaultValues: {
      operationKey: props.action.key,
      timeoutSeconds: props.recovery?.timeoutSeconds ?? 7200,
      parametersText: props.recovery ? "{}" : (props.editor.parametersText ?? "{}"),
    },
  });
  const [review, setReview] = useState<NvkeRequestValues | null>(null);
  return (
    <>
      <p role="note" className="text-warning-fg text-sm">
        {t(
          props.editor.concurrency === "observed_only"
            ? "observedConcurrencyHelp"
            : "conditionalConcurrencyHelp"
        )}
      </p>
      <form
        className="min-w-0 space-y-4"
        onSubmit={form.handleSubmit((values) => {
          if (props.allowed) setReview(values);
        })}
      >
        <div className="space-y-2">
          <Label htmlFor="nvke-resource-timeout">{t("timeout")}</Label>
          <Input
            id="nvke-resource-timeout"
            type="number"
            min={30}
            max={86400}
            disabled={props.pending || Boolean(props.recovery)}
            {...form.register("timeoutSeconds", { valueAsNumber: true })}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="nvke-resource-parameters">{t("parameters")}</Label>
          <Textarea
            id="nvke-resource-parameters"
            className="min-h-64 font-mono text-xs"
            autoComplete="off"
            autoCorrect="off"
            spellCheck={false}
            disabled={props.pending}
            {...form.register("parametersText")}
          />
        </div>
        {Object.keys(form.formState.errors).length > 0 && (
          <p role="alert" className="text-danger-fg text-sm">
            {t("invalidForm")}
          </p>
        )}
        <Button type="submit" disabled={!props.allowed}>
          {t(props.pending ? "working" : "reviewRequest")}
        </Button>
      </form>
      <details className="min-w-0 text-sm">
        <summary className="cursor-pointer font-medium">{t("schema")}</summary>
        <pre className="bg-muted mt-3 max-h-80 overflow-auto rounded-lg p-3 font-mono text-xs [overflow-wrap:anywhere] whitespace-pre-wrap">
          {props.schemaText}
        </pre>
      </details>
      <ConfirmDialog
        open={review !== null}
        onOpenChange={(open) => {
          if (!open) setReview(null);
        }}
        title={t("reviewRequest")}
        description={
          <>
            {t("mutationContext", {
              site: props.siteName,
              action: props.action.key,
              version: props.recovery?.expectedSiteVersion ?? props.editor.siteVersion,
            })}
            <span className="mt-2 block [overflow-wrap:anywhere]">{props.target}</span>
          </>
        }
        confirmLabel={t("submitRequest")}
        destructive={props.action.verb === "DELETE" || props.action.verb === "delete"}
        confirmDisabled={!props.allowed}
        onConfirm={async () => (props.allowed && review ? props.onRequest(review) : false)}
      />
    </>
  );
}
