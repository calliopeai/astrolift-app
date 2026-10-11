"use client";

import { useId, useLayoutEffect, useRef, useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { LinkIcon } from "lucide-react";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { ListPage } from "@/components/list/ListPage";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { ModelPage, SubscriptionModel } from "./ModelSubscriptionsPanel";

export type ConnectionDestination = {
  id: string;
  version: number;
  appVersion: number;
  clusterId: string;
  appSlug: string;
  environmentName: string;
  eligible: boolean;
  action: "AUTO" | "REQUEST" | "DENY";
  policyVersion: string | null;
  reason: string | null;
};
export type ConnectionIntakeReview = {
  organizationId: string;
  modelId: string;
  modelVersion: number;
  clusterId: string;
  providerId: string;
  environmentId: string;
  environmentVersion: number;
  appVersion: number;
  alias: string;
  action: "AUTO" | "REQUEST";
  policyVersion: string;
};
export type ConnectionWriteResult =
  | {
      accepted: true;
      kind: "REQUEST" | "AUTO";
      id?: string;
      version?: number;
      subscriptionId?: string | null;
      correlated?: boolean;
    }
  | { accepted: false; message: string };
export type ModelConnectionIntakeProps = {
  scopeKey: string;
  deployment: SubscriptionModel;
  supported: boolean | null;
  supportError: string | null;
  onRetrySupport: () => void;
  targets: ModelPage<ConnectionDestination>;
  recovery: { environmentId: string; alias: string } | null;
  onRestoreRecovery: () => { environmentId: string; alias: string } | null;
  onDiscardRecovery: () => void;
  onSubmit: (review: ConnectionIntakeReview) => Promise<ConnectionWriteResult>;
  onAccepted: () => Promise<unknown> | unknown;
};
const schema = z.object({
  environmentId: z.string().min(1),
  alias: z.string().regex(/^[a-z][a-z0-9_]{0,31}$/),
});

export function ModelConnectionIntakePanel(props: ModelConnectionIntakeProps) {
  return <Intake key={props.scopeKey} {...props} />;
}
function Intake(props: ModelConnectionIntakeProps) {
  const native = useTranslations("models.native.details");
  const t = useTranslations("models.shared.connections"),
    id = useId();
  const form = useForm<z.infer<typeof schema>>({
    resolver: zodResolver(schema),
    defaultValues: { environmentId: "", alias: "" },
  });
  const environmentId = useWatch({ control: form.control, name: "environmentId" });
  const target = props.targets.rows.find((row) => row.id === environmentId);
  const [review, setReview] = useState<{
    input: ConnectionIntakeReview;
    label: string;
    lease: number;
  } | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [receipt, setReceipt] = useState<{
    kind: "AUTO" | "REQUEST";
    id?: string;
    version?: number;
    subscriptionId?: string | null;
    correlated?: boolean;
    refreshFailed: boolean;
  } | null>(null);
  const snapshot = JSON.stringify([
    props.deployment,
    props.supported,
    props.supportError,
    props.targets.rows,
    props.targets.loading,
    props.targets.stale,
    props.targets.error,
  ]);
  const [lease, setLease] = useState({ key: snapshot, revision: 0 });
  if (lease.key !== snapshot) setLease({ key: snapshot, revision: lease.revision + 1 });
  const latest = useRef({ props, revision: lease.revision }),
    mounted = useRef(false);
  useLayoutEffect(() => {
    latest.current = { props, revision: lease.revision };
  });
  useLayoutEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const ready =
    props.supported === true &&
    !props.supportError &&
    !props.targets.loading &&
    !props.targets.stale &&
    !props.targets.error;
  const selectable = (row: ConnectionDestination) =>
    ready &&
    row.eligible &&
    row.action !== "DENY" &&
    !!row.policyVersion &&
    row.clusterId === props.deployment.clusterId;
  const current = review !== null && review.lease === lease.revision && ready;
  async function confirm() {
    const candidate = review;
    if (!candidate || !mounted.current || candidate.lease !== latest.current.revision) {
      setFailure(t("changed"));
      return false;
    }
    setFailure(null);
    let result: ConnectionWriteResult;
    try {
      result = await latest.current.props.onSubmit(candidate.input);
    } catch {
      if (mounted.current) setFailure(t("uncertain"));
      return false;
    }
    if (!mounted.current || candidate.lease !== latest.current.revision) return false;
    if (!result.accepted) {
      setFailure(result.message);
      return false;
    }
    setReceipt({ ...result, refreshFailed: false });
    setReview(null);
    try {
      await latest.current.props.onAccepted();
    } catch {
      if (mounted.current) setReceipt({ ...result, refreshFailed: true });
    }
    return true;
  }
  if (props.supported !== true)
    return (
      <section className="space-y-3" aria-label={t("add")}>
        <p role={props.supportError ? "alert" : "status"}>
          {props.supportError ??
            t(props.supported === false ? "unsupported" : "capabilityChecking")}
        </p>
        <Button variant="outline" onClick={props.onRetrySupport}>
          {t("retry")}
        </Button>
      </section>
    );
  return (
    <section className="space-y-4" aria-label={t("add")}>
      <p className="text-muted-foreground text-sm">{t("readOnlyEvidence")}</p>
      {props.recovery && (
        <div className="space-y-2" role="status">
          <p>{t("recoveryAvailable")}</p>
          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline"
              onClick={() => {
                const saved = props.onRestoreRecovery();
                if (saved) {
                  form.reset(saved);
                  setReview(null);
                  setFailure(null);
                }
              }}
            >
              {t("restoreReview")}
            </Button>
            <Button variant="ghost" onClick={props.onDiscardRecovery}>
              {t("discardRecovery")}
            </Button>
          </div>
          <p className="text-muted-foreground text-sm">{t("discardRecoveryNotice")}</p>
        </div>
      )}
      <ListPage
        embedded
        {...props.targets}
        label={t("target")}
        getRowId={(row) => row.id}
        empty={{ icon: <LinkIcon />, title: t("chooseTarget") }}
        columns={[
          {
            id: "target",
            header: t("target"),
            cell: (row) => (
              <Button
                variant="ghost"
                disabled={!selectable(row)}
                onClick={() => form.setValue("environmentId", row.id, { shouldValidate: true })}
              >
                {row.appSlug} / {row.environmentName}
              </Button>
            ),
          },
          {
            id: "action",
            header: t("action"),
            cell: (row) => (
              <div>
                <p>
                  {t(
                    row.action === "AUTO" ? "auto" : row.action === "REQUEST" ? "approval" : "deny"
                  )}
                </p>
                {row.reason && <p className="text-muted-foreground text-sm">{row.reason}</p>}
              </div>
            ),
          },
        ]}
      />
      <p role="status">
        {target ? `${target.appSlug} / ${target.environmentName}` : t("chooseTarget")}
      </p>
      <form
        className="space-y-3"
        onSubmit={form.handleSubmit((draft) => {
          const selected = props.targets.rows.find((row) => row.id === draft.environmentId);
          if (
            !selected ||
            !selectable(selected) ||
            selected.action === "DENY" ||
            !selected.policyVersion
          ) {
            setFailure(t("changed"));
            return;
          }
          setFailure(null);
          setReceipt(null);
          setReview({
            lease: lease.revision,
            label: `${selected.appSlug} / ${selected.environmentName}`,
            input: {
              organizationId: props.deployment.organizationId,
              modelId: props.deployment.id,
              modelVersion: props.deployment.version,
              clusterId: props.deployment.clusterId,
              providerId: props.deployment.providerId,
              environmentId: selected.id,
              environmentVersion: selected.version,
              appVersion: selected.appVersion,
              alias: draft.alias,
              action: selected.action,
              policyVersion: selected.policyVersion,
            },
          });
        })}
      >
        <Label htmlFor={`${id}-alias`}>{t("alias")}</Label>
        <Input id={`${id}-alias`} {...form.register("alias")} disabled={!ready} maxLength={32} />
        <p className="text-muted-foreground text-sm">{t("aliasHelp")}</p>
        {form.formState.errors.alias && <p role="alert">{t("invalidAlias")}</p>}
        <Button type="submit" disabled={!ready || !target || !selectable(target)}>
          {t("review")}
        </Button>
      </form>
      {receipt && (
        <div role="status" className="space-y-2">
          <p>
            {t(
              receipt.correlated === false
                ? "acceptedUnverified"
                : receipt.subscriptionId
                  ? "connectionRecorded"
                  : receipt.kind === "REQUEST"
                    ? "requestSaved"
                    : "connectionQueued"
            )}
          </p>
          {receipt.refreshFailed && <p>{t("refreshFailed")}</p>}
          {receipt.kind === "REQUEST" && receipt.id && (
            <Link
              href={`/models/connections/${encodeURIComponent(receipt.id)}?version=${receipt.version}`}
            >
              {t("openRequest")}
            </Link>
          )}
        </div>
      )}
      {failure && (
        <p role="alert" className="text-destructive">
          {failure}
        </p>
      )}
      <ConfirmDialog
        key={review?.lease ?? "closed"}
        open={Boolean(review)}
        confirmDisabled={!current}
        title={t(review?.input.action === "REQUEST" ? "reviewRequest" : "reviewAuto")}
        description={
          <div className="space-y-2">
            <p>
              {review &&
                t("reviewTarget", {
                  model: props.deployment.name,
                  target: review.label,
                  alias: review.input.alias,
                })}
            </p>
            <p>
              {review?.input.action === "REQUEST"
                ? t("requestNotice")
                : props.deployment.nativeConnection
                  ? native("restartImpact")
                  : t("restartNotice")}
            </p>
            {!current && <p>{t("changed")}</p>}
          </div>
        }
        confirmLabel={t(review?.input.action === "REQUEST" ? "requestApproval" : "connect")}
        onConfirm={confirm}
        onOpenChange={(open) => {
          if (!open) setReview((existing) => (existing === review ? null : existing));
        }}
      />
    </section>
  );
}
