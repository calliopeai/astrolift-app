"use client";
import { useId, useLayoutEffect, useRef, useState } from "react";
import { useTranslations, useFormatter } from "next-intl";
import { useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Section } from "@/components/ui/section";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { QueryError } from "@/components/QueryError";
import {
  validSharedPromptLimits,
  type SharedPromptReadiness,
  type SharedPromptResult,
} from "./shared-model-prompt";
export type SharedModelPromptPanelProps = {
  identity: {
    organizationId: string;
    id: string;
    version: number;
    clusterId: string;
    providerId: string;
  };
  readiness: { data: SharedPromptReadiness | null; loading: boolean; error: string | null };
  onCheck: () => void;
  onRun: (prompt: string) => Promise<SharedPromptResult>;
};
export function SharedModelPromptPanel(props: SharedModelPromptPanelProps) {
  return <PromptContext key={JSON.stringify(props.identity)} {...props} />;
}
function PromptContext({ identity, readiness, onCheck, onRun }: SharedModelPromptPanelProps) {
  const t = useTranslations("models.shared.prompt"),
    format = useFormatter(),
    id = useId();
  const schema = z.object({ prompt: z.string().trim().min(1).max(4000) });
  const form = useForm<z.infer<typeof schema>>({
    resolver: zodResolver(schema),
    defaultValues: { prompt: "" },
  });
  const prompt = useWatch({ control: form.control, name: "prompt" });
  const [result, setResult] = useState<SharedPromptResult | null>(null),
    [pending, setPending] = useState(false);
  const scopeKey = JSON.stringify([identity, readiness]),
    [scope, setScope] = useState({ key: scopeKey, revision: 0 });
  if (scope.key !== scopeKey) setScope({ key: scopeKey, revision: scope.revision + 1 });
  const lifecycle = useRef(0);
  const mounted = useRef(true),
    busy = useRef(false),
    latest = useRef({ revision: scope.revision, readiness, onRun });
  useLayoutEffect(() => {
    latest.current = { revision: scope.revision, readiness, onRun };
  });
  useLayoutEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      lifecycle.current += 1;
    };
  }, []);
  const admission = validSharedPromptLimits(readiness.data) ? readiness.data : null;
  const allowed =
    admission &&
    admission.eligible &&
    admission.state === "READY" &&
    !readiness.loading &&
    !readiness.error;
  const text = prompt.trim(),
    invalid = !text || !admission || text.length > admission.maxPromptChars;
  async function send() {
    if (busy.current || !allowed || invalid) return;
    const candidate = latest.current,
      revision = candidate.revision,
      lifecycleRevision = lifecycle.current;
    busy.current = true;
    setPending(true);
    setResult(null);
    try {
      const reply = await candidate.onRun(text);
      if (
        !mounted.current ||
        lifecycle.current !== lifecycleRevision ||
        latest.current.revision !== revision
      )
        return;
      setResult(reply);
    } catch (error) {
      if (
        mounted.current &&
        lifecycle.current === lifecycleRevision &&
        latest.current.revision === revision
      )
        setResult({
          ok: false,
          message: error instanceof Error ? error.message : t("failed"),
          code: null,
        });
    } finally {
      busy.current = false;
      if (mounted.current) setPending(false);
    }
  }
  return (
    <Section title={t("title")} description={t("description")}>
      <div className="space-y-4">
        {readiness.error && <QueryError title={t("readError")} error={readiness.error} />}
        {readiness.loading ? (
          <p role="status">{t("loading")}</p>
        ) : (
          readiness.data && <p role="note">{t(`states.${readiness.data.state}`)}</p>
        )}
        {admission && (
          <p className="text-muted-foreground text-sm">
            {t("limits", {
              chars: admission.maxPromptChars,
              tokens: admission.maxOutputTokens,
              perMinute: admission.promptsPerMinute,
              seconds: admission.maxWaitSeconds,
            })}
          </p>
        )}
        <Button variant="outline" onClick={onCheck} disabled={pending || readiness.loading}>
          {t("refresh")}
        </Button>
        <form
          onSubmit={(event) => {
            void form.handleSubmit(send)(event);
          }}
          className="space-y-3"
        >
          <Label htmlFor={id}>{t("prompt")}</Label>
          <Textarea
            id={id}
            {...form.register("prompt")}
            disabled={pending}
            maxLength={admission ? admission.maxPromptChars : 4000}
            aria-invalid={Boolean(form.formState.errors.prompt)}
            aria-describedby={form.formState.errors.prompt ? `${id}-error` : undefined}
          />
          {form.formState.errors.prompt && (
            <p id={`${id}-error`} role="alert">
              {t("invalid")}
            </p>
          )}
          <Button type="submit" disabled={pending || !allowed || invalid}>
            {t("send")}
          </Button>
        </form>
        {pending && <p role="status">{t("waiting")}</p>}
        {result &&
          (result.ok ? (
            <div className="space-y-2">
              <h3 className="font-medium">{t("result")}</h3>
              <p className="break-words whitespace-pre-wrap">{result.reply}</p>
              <div className="text-muted-foreground flex flex-wrap gap-4 text-sm">
                {result.latencyMs !== null && (
                  <p>{t("latency", { value: format.number(result.latencyMs) })}</p>
                )}
                {result.totalTokens !== null && (
                  <p>{t("tokens", { value: format.number(result.totalTokens) })}</p>
                )}
              </div>
            </div>
          ) : (
            <div role="alert" className="space-y-2 break-words">
              <p>{result.message}</p>
              {result.code && <code>{result.code}</code>}
              {result.currentVersion != null && (
                <p>
                  <code>currentVersion: {result.currentVersion}</code>
                </p>
              )}
              {result.requestedVersion != null && (
                <p>
                  <code>requestedVersion: {result.requestedVersion}</code>
                </p>
              )}
            </div>
          ))}
      </div>
    </Section>
  );
}
