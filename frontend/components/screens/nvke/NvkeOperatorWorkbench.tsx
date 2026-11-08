"use client";

import { useEffect, useRef, useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
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
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { nativeTextDigest } from "@/lib/nvke/recovery";
import { operatorEnvelopeMatches, type NvkeOperatorJobValues } from "@/lib/nvke/operator-admission";
import {
  operatorTemplateText,
  type NvkeOperatorContract,
  type NvkeOperatorCommand,
} from "@/lib/nvke/operator-contract";
import { operatorJobFormSchema } from "./schema";
import type { NvkeSiteView } from "./types";
import type { NvkeOperatorAgentAuthority } from "./NvkeOperatorAgentPanel";

export interface NvkeOperatorWorkbenchProps {
  scopeKey: string;
  site: Pick<NvkeSiteView, "id" | "name" | "version" | "upstreamRevision" | "isActive">;
  authority: NvkeOperatorAgentAuthority | null;
  contract: NvkeOperatorContract | null;
  loading: boolean;
  pending: boolean;
  error: string | null;
  canManage: boolean;
  canRecover: boolean;
  recovery: {
    requestId: string;
    operationKey: string;
    timeoutSeconds: number;
    acknowledgeInfrastructureChange: boolean;
    expectedSiteVersion: number;
    authorityVersion: number;
  } | null;
  onRequest: (values: NvkeOperatorJobValues) => Promise<boolean>;
  onRecover: () => Promise<boolean>;
  onRefresh: () => void;
}

export function NvkeOperatorWorkbench(props: NvkeOperatorWorkbenchProps) {
  return (
    <WorkbenchEditor
      key={JSON.stringify([
        props.scopeKey,
        props.site.id,
        props.site.version,
        props.site.upstreamRevision,
        props.authority,
        props.contract,
        props.canManage,
        props.loading,
        props.pending,
        props.recovery,
      ])}
      {...props}
    />
  );
}

function WorkbenchEditor(props: NvkeOperatorWorkbenchProps) {
  const t = useTranslations("nvke"),
    w = useTranslations("nvke.operatorWorkbench");
  const first =
    props.contract?.commands.find((row) => `tooling.${row.key}` === props.recovery?.operationKey) ??
    props.contract?.commands[0];
  const initial = (command?: NvkeOperatorCommand): NvkeOperatorJobValues => ({
    operationKey: props.recovery?.operationKey ?? (command ? `tooling.${command.key}` : ""),
    requestText: props.recovery
      ? ""
      : command
        ? operatorTemplateText(command, props.site.id, props.site.upstreamRevision)
        : "",
    timeoutSeconds: props.recovery?.timeoutSeconds ?? 7200,
    acknowledgeInfrastructureChange: props.recovery?.acknowledgeInfrastructureChange ?? false,
  });
  const form = useForm<NvkeOperatorJobValues>({
    resolver: zodResolver(operatorJobFormSchema),
    defaultValues: initial(first),
  });
  const operationKey = useWatch({ control: form.control, name: "operationKey" });
  const selected = props.contract?.commands.find((row) => `tooling.${row.key}` === operationKey);
  const [review, setReview] = useState<{
    command: string;
    digest: string;
    bytes: number;
    timeout: number;
    acknowledged: boolean;
    effectful: boolean;
  } | null>(null);
  const snapshot = useRef<NvkeOperatorJobValues | null>(null),
    ticket = useRef(0);
  const [working, setWorking] = useState(false),
    [unconfirmed, setUnconfirmed] = useState(false);
  const [switchTo, setSwitchTo] = useState<NvkeOperatorCommand | null>(null);
  useEffect(
    () => () => {
      ticket.current++;
      snapshot.current = null;
    },
    []
  );
  const mayRequest =
    props.canManage &&
    !props.loading &&
    !props.pending &&
    !props.error &&
    !unconfirmed &&
    props.site.isActive &&
    props.authority?.isEnabled === true &&
    !!props.authority.verifiedAt &&
    props.authority.verificationCode === "AGENT_VERIFIED" &&
    props.authority.siteId === props.site.id &&
    props.authority.siteVersion === props.site.version &&
    props.contract?.revision === props.site.upstreamRevision;
  const choose = (command: NvkeOperatorCommand) => {
    snapshot.current = null;
    ticket.current++;
    form.reset(initial(command));
    setSwitchTo(null);
  };
  return (
    <Section title={w("title")} description={w("help")}>
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" disabled={props.pending || working} onClick={props.onRefresh}>
          {t("refresh")}
        </Button>
        {props.recovery && (
          <Button
            variant="outline"
            disabled={!props.canRecover || props.pending || working}
            onClick={async () => {
              setWorking(true);
              try {
                if (!(await props.onRecover())) setUnconfirmed(true);
              } catch {
                setUnconfirmed(true);
              } finally {
                setWorking(false);
              }
            }}
          >
            {w("recover")}
          </Button>
        )}
      </div>
      {props.loading ? (
        <Skeleton className="h-32" />
      ) : (
        <>
          {props.error && (
            <p role="alert" className="text-danger-fg text-sm">
              {props.error}
            </p>
          )}
          {unconfirmed && !props.error && (
            <p role="alert" className="text-danger-fg text-sm">
              {t("requestFailed")}
            </p>
          )}
          {props.recovery && (
            <p role="status" className="text-warning-fg text-sm [overflow-wrap:anywhere]">
              {w("recovery", {
                id: props.recovery.requestId,
                command: props.recovery.operationKey,
              })}
            </p>
          )}
          {!mayRequest && !props.error && (
            <p className="text-muted-foreground text-sm">{w("unavailable")}</p>
          )}
          <form
            noValidate
            className="flex min-w-0 flex-col gap-4"
            onSubmit={(event) => {
              void form.handleSubmit(async (values) => {
                if (
                  !mayRequest ||
                  working ||
                  !selected ||
                  !operatorEnvelopeMatches(
                    values.requestText,
                    values.operationKey,
                    props.site.id,
                    props.site.upstreamRevision
                  )
                ) {
                  form.setError("requestText", { type: "validate" });
                  return;
                }
                const effectful =
                  selected.infrastructureChange &&
                  JSON.parse(values.requestText).options["dry-run"] !== true;
                if (effectful && !values.acknowledgeInfrastructureChange) {
                  form.setError("acknowledgeInfrastructureChange", { type: "validate" });
                  return;
                }
                const saved = { ...values },
                  attempt = ++ticket.current;
                setWorking(true);
                try {
                  const digest = await nativeTextDigest(saved.requestText);
                  if (ticket.current !== attempt) return;
                  snapshot.current = saved;
                  form.setValue("requestText", "");
                  setReview({
                    command: selected.key,
                    digest,
                    bytes: new TextEncoder().encode(saved.requestText).length,
                    timeout: saved.timeoutSeconds,
                    acknowledged: saved.acknowledgeInfrastructureChange,
                    effectful,
                  });
                } catch {
                  if (ticket.current === attempt)
                    form.setError("requestText", { type: "validate" });
                } finally {
                  if (ticket.current === attempt) setWorking(false);
                }
              })(event);
            }}
          >
            <div className="grid min-w-0 gap-4 md:grid-cols-[minmax(0,1fr)_12rem]">
              <div className="min-w-0 space-y-2">
                <Label htmlFor="nvke-tooling-command">{w("command")}</Label>
                <Select
                  value={selected?.key ?? ""}
                  disabled={!mayRequest || working || !!props.recovery}
                  onValueChange={(key) => {
                    const command = props.contract?.commands.find((row) => row.key === key);
                    if (command) {
                      if (form.formState.isDirty) setSwitchTo(command);
                      else choose(command);
                    }
                  }}
                >
                  <SelectTrigger id="nvke-tooling-command" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {props.contract?.commands.map((row) => (
                      <SelectItem key={row.key} value={row.key}>
                        {row.key}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label htmlFor="nvke-tooling-timeout">{t("timeout")}</Label>
                <Input
                  id="nvke-tooling-timeout"
                  type="number"
                  min={30}
                  max={86400}
                  disabled={!mayRequest || working || !!props.recovery}
                  {...form.register("timeoutSeconds", { valueAsNumber: true })}
                />
              </div>
            </div>
            <div className="space-y-2">
              <Label htmlFor="nvke-tooling-import">{w("import")}</Label>
              <Input
                id="nvke-tooling-import"
                type="file"
                accept=".json,application/json"
                disabled={!mayRequest || working || !!review}
                onChange={async (event) => {
                  const input = event.currentTarget,
                    file = input.files?.[0];
                  input.value = "";
                  if (!file || !mayRequest || working) return;
                  const attempt = ++ticket.current;
                  setWorking(true);
                  try {
                    if (file.size > 1048576) throw new Error("INVALID_INPUT");
                    const text = new TextDecoder("utf-8", { fatal: true }).decode(
                      await file.arrayBuffer()
                    );
                    const envelope = JSON.parse(text),
                      command = props.contract?.commands.find(
                        (row) => row.key === envelope.command
                      );
                    if (
                      !command ||
                      !operatorEnvelopeMatches(
                        text,
                        `tooling.${command.key}`,
                        props.site.id,
                        props.site.upstreamRevision
                      ) ||
                      (props.recovery && props.recovery.operationKey !== `tooling.${command.key}`)
                    )
                      throw new Error("INVALID_INPUT");
                    if (ticket.current !== attempt) return;
                    snapshot.current = null;
                    form.setValue("operationKey", `tooling.${command.key}`, { shouldDirty: true });
                    form.setValue("requestText", text, { shouldDirty: true, shouldValidate: true });
                  } catch {
                    if (ticket.current === attempt)
                      form.setError("requestText", { type: "validate" });
                  } finally {
                    if (ticket.current === attempt) setWorking(false);
                  }
                }}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="nvke-tooling-request">{w("request")}</Label>
              <Textarea
                id="nvke-tooling-request"
                autoComplete="off"
                autoCorrect="off"
                spellCheck={false}
                className="min-h-64 font-mono text-xs"
                disabled={!mayRequest || working || !!review}
                {...form.register("requestText")}
              />
              <p className="text-muted-foreground text-xs">{w("privateHelp")}</p>
            </div>
            {selected?.infrastructureChange && (
              <div className="flex items-start gap-2">
                <input
                  id="nvke-tooling-acknowledge"
                  type="checkbox"
                  disabled={!mayRequest || working || !!review || !!props.recovery}
                  {...form.register("acknowledgeInfrastructureChange")}
                />
                <Label htmlFor="nvke-tooling-acknowledge">{w("acknowledge")}</Label>
              </div>
            )}
            {Object.keys(form.formState.errors).length > 0 && (
              <p role="alert" className="text-danger-fg text-sm">
                {t("invalidForm")}
              </p>
            )}
            {props.canManage && (
              <Button type="submit" disabled={!mayRequest || working || !!review}>
                {t(props.recovery ? "retryRequest" : "reviewRequest")}
              </Button>
            )}
          </form>
          {selected && (
            <details className="min-w-0 text-sm">
              <summary className="cursor-pointer font-medium">{w("options")}</summary>
              <p className="mt-2 text-xs">{w("references")}</p>
              <pre className="bg-muted mt-3 max-h-80 overflow-auto rounded-lg p-3 font-mono text-xs [overflow-wrap:anywhere] whitespace-pre-wrap">
                {JSON.stringify(selected, null, 2)}
              </pre>
            </details>
          )}
        </>
      )}
      <ConfirmDialog
        open={!!review}
        onOpenChange={(open) => {
          if (!open) {
            ticket.current++;
            snapshot.current = null;
            setReview(null);
          }
        }}
        title={t("reviewRequest")}
        confirmLabel={t("submitRequest")}
        destructive={review?.effectful}
        confirmDisabled={!mayRequest || working}
        description={
          <>
            {w("review", {
              site: props.site.name,
              siteVersion: props.site.version,
              authorityVersion: props.authority?.version ?? 0,
              command: review?.command ?? "",
              revision: props.site.upstreamRevision,
              endpoint: props.authority?.endpoint ?? "—",
              instance: props.authority?.instanceId ?? "—",
              bundle: props.authority?.bundleManifestSha256 ?? "—",
              digest: review?.digest ?? "",
              bytes: review?.bytes ?? 0,
              timeout: review?.timeout ?? 0,
            })}
            <span className="mt-2 block">{w(review?.effectful ? "effect" : "utility")}</span>
            {review?.acknowledged && <span className="mt-2 block">{w("acknowledge")}</span>}
          </>
        }
        onConfirm={async () => {
          if (!mayRequest || working) return false;
          const values = snapshot.current;
          snapshot.current = null;
          if (!values) {
            setReview(null);
            return false;
          }
          setWorking(true);
          try {
            const ok = await props.onRequest(values);
            if (!ok) setUnconfirmed(true);
            return ok;
          } catch {
            setUnconfirmed(true);
            return false;
          } finally {
            setWorking(false);
            form.setValue("requestText", "");
            setReview(null);
          }
        }}
      />
      <ConfirmDialog
        open={!!switchTo}
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
