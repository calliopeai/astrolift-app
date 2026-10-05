"use client";
import { MailIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import type { ListStateController } from "@/components/list/list-state";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { useFormatters } from "@/lib/i18n/formatters";
import type {
  InstallAlertMailSupportQuery,
  InstallAlertMailHistoryQuery,
} from "@/graphql/__generated__/operations";
export const INSTALL_MAIL_EVENTS = [
  "deploy.failed",
  "app.down",
  "app.recovered",
  "domain.cert_expiring",
  "domain.cert_renewal_failed",
  "cluster.bootstrap_failed",
  "secret.revealed",
  "app.deregister_pending",
] as const;
export type InstallMailEvent = (typeof INSTALL_MAIL_EVENTS)[number];
export type AlertMailSupport = InstallAlertMailSupportQuery["installAlertMailSupport"];
export type AlertMailTest =
  InstallAlertMailHistoryQuery["installAlertMailTestsPage"]["items"][number];
export type InstallAlertMailPanelProps = {
  event: InstallMailEvent;
  onEvent: (event: InstallMailEvent) => void;
  support: AlertMailSupport | null;
  loading: boolean;
  supportError: boolean;
  reviewed: boolean;
  busy: boolean;
  locked: boolean;
  canReview: boolean;
  canSend: boolean;
  requestId: string | null;
  current: AlertMailTest | null;
  message: "uncertain" | "sourceChanged" | "recordUnverified" | "storageUnavailable" | null;
  canStartAnother: boolean;
  onReview: () => void;
  onSend: () => void;
  onRefresh: () => void;
  onStartAnother: () => void;
  history: {
    list: ListStateController;
    rows: AlertMailTest[];
    loading: boolean;
    stale: boolean;
    error: { message: string } | null;
    totalCount: number | null;
    nextCursor: string | null;
    refetch: () => void;
  };
};
const STATUS = {
  reserved: "reserved",
  sent: "sent",
  accepted: "accepted",
  failed: "failed",
  unknown: "unknown",
} as const;
export function InstallAlertMailPanel(p: InstallAlertMailPanelProps) {
  const t = useTranslations("installAlertMail"),
    fmt = useFormatters();
  const admitted =
    p.support?.allowed === true && !p.loading && !p.supportError && p.support.transport === "smtp";
  const columns: Column<AlertMailTest>[] = [
    {
      id: "status",
      header: t("outcome"),
      cell: (r) => (r.status in STATUS ? t(STATUS[r.status as keyof typeof STATUS]) : t("unknown")),
    },
    { id: "recipient", header: t("recipient"), cell: (r) => r.recipient },
    { id: "sender", header: t("sender"), cell: (r) => r.sender },
    { id: "created", header: t("created"), cell: (r) => fmt.formatDateTime(r.createdAt) },
    { id: "reason", header: t("reason"), cell: (r) => r.reasonCode || "—" },
  ];
  return (
    <section aria-label={t("title")} className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="font-semibold">{t("title")}</h2>
          <p className="text-muted-foreground max-w-prose text-sm">{t("intro")}</p>
        </div>
        <Button variant="outline" onClick={p.onRefresh} disabled={p.busy}>
          {t("refresh")}
        </Button>
      </div>
      <div className="grid max-w-lg gap-1">
        <Label htmlFor="install-mail-event">{t("event")}</Label>
        <select
          id="install-mail-event"
          className="border-input bg-background w-full rounded-md border px-3 py-2 text-sm"
          value={p.event}
          disabled={p.locked || p.busy}
          onChange={(e) => p.onEvent(e.target.value as InstallMailEvent)}
        >
          {INSTALL_MAIL_EVENTS.map((e, i) => (
            <option key={e} value={e}>
              {t(`event${i}`)}
            </option>
          ))}
        </select>
        <p className="text-muted-foreground text-sm">{t("preferences")}</p>
      </div>
      {p.loading ? (
        <p role="status">{t("checking")}</p>
      ) : p.supportError ? (
        <p role="alert">{t("unavailable")}</p>
      ) : p.support?.allowed !== true ? (
        <div role="status">
          <p>{t("unsupported")}</p>
          {p.support?.reason ? (
            <p className="font-mono text-xs break-all">{p.support.reason}</p>
          ) : null}
        </div>
      ) : null}
      {p.support ? (
        <dl className="grid gap-3 sm:grid-cols-2">
          {(
            [
              ["sender", p.support.sender],
              ["recipient", p.support.recipient],
              ["transport", p.support.transport],
              ["tls", p.support.tlsMode],
              ["checked", fmt.formatDateTime(p.support.checkedAt)],
            ] as const
          ).map(([k, v]) => (
            <div key={k}>
              <dt className="text-muted-foreground text-xs">{t(k)}</dt>
              <dd className="text-sm break-all">{v || "—"}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      <p className="text-muted-foreground max-w-prose text-sm">{t("reviewHelp")}</p>
      <div className="flex flex-wrap gap-2">
        <Button
          variant="outline"
          disabled={!admitted || !p.canReview || p.busy}
          onClick={p.onReview}
        >
          {t("review")}
        </Button>
        <Button disabled={!admitted || !p.canSend || p.busy} onClick={p.onSend}>
          {p.busy ? t("sending") : t("send")}
        </Button>
      </div>
      {p.reviewed ? <p role="status">{t("reviewed")}</p> : null}
      {p.requestId ? (
        <p className="text-xs break-all">
          {t("request")}: {p.requestId}
        </p>
      ) : null}
      {p.message ? (
        <p role="alert" className="max-w-prose text-sm">
          {t(p.message)}
        </p>
      ) : null}
      {p.current ? (
        <div className="space-y-2 rounded-md border p-3">
          <p className="font-medium">
            {p.current.status in STATUS
              ? t(STATUS[p.current.status as keyof typeof STATUS])
              : t("unknown")}
          </p>
          <p className="text-sm">{t("acceptance")}</p>
          {p.current.reasonCode ? (
            <p className="font-mono text-xs break-all">{p.current.reasonCode}</p>
          ) : null}
        </div>
      ) : null}
      {p.canStartAnother ? (
        <Button variant="outline" onClick={p.onStartAnother} disabled={p.busy}>
          {t("another")}
        </Button>
      ) : null}
      <ListPage
        embedded
        label={t("history")}
        list={p.history.list}
        rows={p.history.rows}
        columns={columns}
        getRowId={(r) => r.id}
        loading={p.history.loading}
        stale={p.history.stale}
        error={p.history.error}
        onRetry={p.history.refetch}
        totalCount={p.history.totalCount}
        nextCursor={p.history.nextCursor}
        empty={{
          icon: <MailIcon className="size-5" />,
          title: t("empty"),
          description: t("emptyHelp"),
        }}
      />
    </section>
  );
}
