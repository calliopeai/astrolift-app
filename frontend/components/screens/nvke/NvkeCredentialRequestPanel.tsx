"use client";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { useTranslations } from "next-intl";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Section } from "@/components/ui/section";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import type { NvkeSiteView } from "./types";
import { credentialObservationSchema } from "./schema";

export interface NvkeCredentialRequestPanelProps {
  scopeKey: string;
  site: Pick<NvkeSiteView, "id" | "name" | "version">;
  source: { operationId: string; version: number };
  clusterId: string | null;
  authority: { version: number; endpoint: string } | null;
  loading: boolean;
  pending: boolean;
  canManage: boolean;
  canRecover: boolean;
  error: string | null;
  recovery: { requestId: string; operationKey: string; timeoutSeconds: number } | null;
  onRequest: (timeoutSeconds: number) => Promise<boolean>;
  onRecover: () => Promise<boolean>;
  onRefresh: () => void;
}
export function NvkeCredentialRequestPanel(props: NvkeCredentialRequestPanelProps) {
  return (
    <RequestEditor
      key={JSON.stringify([
        props.scopeKey,
        props.site.id,
        props.site.version,
        props.source,
        props.authority,
        props.clusterId,
        props.canManage,
        props.loading,
        props.recovery?.requestId,
      ])}
      {...props}
    />
  );
}
function RequestEditor(props: NvkeCredentialRequestPanelProps) {
  const t = useTranslations("nvke.credentialCollection"),
    common = useTranslations("nvke");
  const [editing, setEditing] = useState(false),
    [review, setReview] = useState<number | null>(null);
  const form = useForm<{ timeoutSeconds: number }>({
    resolver: zodResolver(credentialObservationSchema),
    defaultValues: { timeoutSeconds: props.recovery?.timeoutSeconds ?? 600 },
  });
  const mayRequest = props.canManage && !props.loading && !props.pending && !props.error;
  return (
    <Section title={t("title")} description={t("help")}>
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" disabled={props.pending} onClick={props.onRefresh}>
          {common("refresh")}
        </Button>
        {props.canManage && (
          <Button disabled={!mayRequest} onClick={() => setEditing(true)}>
            {t(props.recovery ? "retry" : "collect")}
          </Button>
        )}
        {props.recovery && (
          <Button
            variant="outline"
            disabled={!props.canRecover || props.pending}
            onClick={() => void props.onRecover()}
          >
            {t("recover")}
          </Button>
        )}
      </div>
      {props.loading && <Skeleton className="h-20" />}
      {props.error && (
        <p role="alert" className="text-danger-fg text-sm">
          {props.error}
        </p>
      )}
      {!props.loading && !props.canManage && !props.recovery && (
        <p className="text-muted-foreground text-sm">{t("unavailable")}</p>
      )}
      {props.recovery && (
        <p role="status" className="text-muted-foreground text-sm">
          {t("pending")}{" "}
          <code className="text-xs [overflow-wrap:anywhere]">
            {props.recovery.operationKey} · {props.recovery.requestId}
          </code>
        </p>
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
            <SheetTitle>{t("collect")}</SheetTitle>
            <SheetDescription>{t("help")}</SheetDescription>
          </SheetHeader>
          <form
            noValidate
            className="flex flex-col gap-4 p-4"
            onSubmit={form.handleSubmit((value) => {
              if (mayRequest) setReview(value.timeoutSeconds);
            })}
          >
            <Label htmlFor="nvke-credential-timeout">{t("deadline")}</Label>
            <Input
              id="nvke-credential-timeout"
              type="number"
              min={30}
              max={86400}
              disabled={!mayRequest || !!props.recovery}
              {...form.register("timeoutSeconds", { valueAsNumber: true })}
            />
            {Object.keys(form.formState.errors).length > 0 && (
              <p role="alert" className="text-danger-fg text-sm">
                {common("invalidForm")}
              </p>
            )}
            <SheetFooter>
              <Button type="submit" disabled={!mayRequest}>
                {common("reviewRequest")}
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
        title={t(props.recovery ? "retry" : "collect")}
        description={t("review", {
          site: props.site.name,
          siteVersion: props.site.version,
          source: props.source.operationId,
          sourceVersion: props.source.version,
          cluster: props.clusterId ?? "—",
          authority: props.authority?.version ?? 0,
          endpoint: props.authority?.endpoint ?? "—",
          timeout: review ?? 600,
        })}
        confirmDisabled={!mayRequest}
        onConfirm={async () => {
          if (!mayRequest || review === null) return false;
          const ok = await props.onRequest(review);
          setReview(null);
          setEditing(false);
          form.reset();
          return ok;
        }}
      />
    </Section>
  );
}
