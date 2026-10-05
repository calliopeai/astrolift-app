"use client";

import { useTranslations } from "next-intl";
import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import type { ListStateController } from "@/components/list/list-state";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useFormatters } from "@/lib/i18n/formatters";
import type {
  EmailDeliverySupportQuery,
  EmailDeliveryTestsPageQuery,
} from "@/graphql/__generated__/operations";

export type DeliveryTest = EmailDeliveryTestsPageQuery["emailDeliveryTestsPage"]["items"][number];
export type DeliverySupport = EmailDeliverySupportQuery["emailDeliveryTestSupport"];
export type DeliveryDraft = { recipient: string; subject: string; body: string };
export type DeliveryHistory = {
  list: ListStateController;
  rows: DeliveryTest[];
  loading: boolean;
  stale: boolean;
  error: { message: string } | null;
  totalCount: number | null;
  nextCursor: string | null;
  refetch: () => void;
};
export type EmailDeliveryPanelProps = {
  serviceName: string;
  support: DeliverySupport | null;
  supportLoading: boolean;
  supportError: string | null;
  draft: DeliveryDraft;
  onDraft: (draft: DeliveryDraft) => void;
  reviewed: boolean;
  canReview: boolean;
  canSend: boolean;
  busy: boolean;
  locked: boolean;
  recovered: boolean;
  requestId: string | null;
  current: DeliveryTest | null;
  message: "uncertain" | "acceptedUnverified" | null;
  actionError: string | null;
  canStartAnother: boolean;
  onReview: () => void;
  onSend: () => void;
  onStartAnother: () => void;
  onRefresh: () => void;
  history: DeliveryHistory;
};
const statuses = {
  submitting: "submitting",
  accepted: "accepted",
  unknown: "unknown",
  failed: "failed",
  suppressed: "suppressed",
  delivered: "delivered",
  deferred: "deferred",
  bounced: "bounced",
  complained: "complained",
  rejected: "rejected",
  observation_timed_out: "observationTimedOut",
} as const;

/** Pure exact-source review and content-free provider-observation history. */
export function EmailDeliveryPanel(p: EmailDeliveryPanelProps) {
  const t = useTranslations("emailDelivery");
  const fmt = useFormatters();
  const admitted =
    p.support?.allowed === true &&
    !p.supportLoading &&
    !p.supportError &&
    Number.isSafeInteger(p.support.serviceVersion) &&
    (p.support.serviceVersion ?? 0) > 0;
  const columns: Column<DeliveryTest>[] = [
    {
      id: "status",
      header: t("status"),
      cell: (r) => (
        <span>
          {r.status in statuses
            ? t(statuses[r.status as keyof typeof statuses])
            : t("unrecognizedStatus")}
        </span>
      ),
    },
    { id: "recipient", header: t("recipient"), cell: (r) => r.recipient },
    { id: "createdAt", header: t("createdAt"), cell: (r) => fmt.formatDateTime(r.createdAt) },
    {
      id: "observedAt",
      header: t("observedAt"),
      cell: (r) => (r.observedAt ? fmt.formatDateTime(r.observedAt) : "—"),
    },
    { id: "reason", header: t("reason"), cell: (r) => r.reasonCode || "—" },
  ];
  const fields = [
    ["sender", p.support?.sender],
    ["identity", p.support?.identity],
    ["account", p.support?.accountId],
    ["region", p.support?.region],
    ["sourceVersion", p.support?.serviceVersion],
  ] as const;
  return (
    <section aria-label={t("title")} className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="font-semibold">{t("title")}</h2>
          <p className="text-muted-foreground text-sm">{t("intro")}</p>
          <p className="text-sm">
            {t("service")}: {p.serviceName}
          </p>
        </div>
        <Button variant="outline" onClick={p.onRefresh} disabled={p.busy}>
          {t("refresh")}
        </Button>
      </div>
      {p.supportLoading ? (
        <p role="status">{t("checking")}</p>
      ) : p.supportError ? (
        <p role="alert">{t("supportUnavailable")}</p>
      ) : !p.support?.allowed ? (
        <Card className="p-3">
          <p>{t("denied")}</p>
          {p.support?.reason ? <p className="font-mono text-xs">{p.support.reason}</p> : null}
        </Card>
      ) : null}
      {p.support ? (
        <dl className="grid gap-2 sm:grid-cols-2">
          {fields.map(([key, value]) => (
            <div key={key}>
              <dt className="text-muted-foreground text-xs">{t(key)}</dt>
              <dd className="text-sm break-all">{value ?? "—"}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      <div className="space-y-3">
        {(["recipient", "subject", "body"] as const).map((key) => (
          <div key={key} className="grid gap-1">
            <Label htmlFor={`email-delivery-${key}`}>
              {t(key)}
              {key !== "recipient" ? ` (${t("optional")})` : ""}
            </Label>
            {key === "body" ? (
              <Textarea
                id={`email-delivery-${key}`}
                value={p.draft[key]}
                onChange={(e) => p.onDraft({ ...p.draft, [key]: e.target.value })}
                disabled={p.locked || p.busy}
                rows={3}
              />
            ) : (
              <Input
                id={`email-delivery-${key}`}
                type={key === "recipient" ? "email" : "text"}
                autoComplete="off"
                value={p.draft[key]}
                onChange={(e) => p.onDraft({ ...p.draft, [key]: e.target.value })}
                disabled={p.locked || p.busy}
              />
            )}
          </div>
        ))}
        <p className="text-muted-foreground text-xs">{t("reviewHelp")}</p>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            onClick={p.onReview}
            disabled={!admitted || !p.canReview || p.busy}
          >
            {t("review")}
          </Button>
          <Button onClick={p.onSend} disabled={!admitted || !p.canSend || p.busy}>
            {p.busy ? t("sending") : p.requestId ? t("retry") : t("send")}
          </Button>
        </div>
        {p.reviewed ? <p role="status">{t("reviewed")}</p> : null}
      </div>
      {p.message ? <p role="status">{t(p.message)}</p> : null}
      {p.actionError ? (
        <p role="alert" className="break-words">
          {p.actionError}
        </p>
      ) : null}
      {p.recovered ? <p role="status">{t("recoverHelp")}</p> : null}
      {p.requestId ? (
        <p className="text-xs">
          {t("currentRequest")}: <span className="font-mono">{p.requestId}</span>
        </p>
      ) : null}
      {p.current ? (
        <Card className="space-y-2 p-3">
          <p>
            {p.current.status in statuses
              ? t(statuses[p.current.status as keyof typeof statuses])
              : t("unrecognizedStatus")}
          </p>
          {p.current.acceptedAt ? <p className="text-sm">{t("acceptedHelp")}</p> : null}
          {p.current.acceptedAt && !p.current.eventTrackingConfigured ? (
            <p className="text-sm">{t("noTracking")}</p>
          ) : null}
          {p.current.simulator ? <p className="text-sm">{t("simulatorHelp")}</p> : null}
          <p className="text-xs">
            {t("providerMessageId")}: {p.current.providerMessageId ?? "—"}
          </p>
        </Card>
      ) : null}
      {p.canStartAnother ? (
        <Button variant="outline" onClick={p.onStartAnother} disabled={p.busy}>
          {t("startAnother")}
        </Button>
      ) : null}
      <ListPage
        embedded
        label={t("history")}
        list={p.history.list}
        rows={p.history.rows}
        loading={p.history.loading}
        stale={p.history.stale}
        error={p.history.error}
        onRetry={p.history.refetch}
        totalCount={p.history.totalCount}
        nextCursor={p.history.nextCursor}
        columns={columns}
        getRowId={(r) => r.id}
        empty={{ icon: null, title: t("emptyHistory"), description: t("intro") }}
      />
    </section>
  );
}
