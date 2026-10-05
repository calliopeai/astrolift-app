"use client";
import * as React from "react";
import { useApolloClient, useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { useLocalListState } from "@/components/list/use-list-state";
import { emailHistoryDefinition } from "@/components/screens/email-delivery/email-delivery-history";
import {
  INSTALL_ALERT_MAIL_SUPPORT,
  INSTALL_ALERT_MAIL_HISTORY,
  SEND_INSTALL_ALERT_MAIL_TEST,
} from "@/graphql/operations/install-alert-mail.queries";
import type {
  InstallAlertMailSupportQuery,
  InstallAlertMailHistoryQuery,
  SendInstallAlertMailTestMutation,
} from "@/graphql/__generated__/operations";
import type {
  AlertMailSupport,
  AlertMailTest,
  InstallAlertMailPanelProps,
  InstallMailEvent,
} from "./InstallAlertMailPanel";
const guid = (v: unknown): v is string =>
  typeof v === "string" && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(v);
type Intent = { format: 1; requestId: string; sourceFingerprint: string; event: InstallMailEvent };
function source(s: AlertMailSupport | null | undefined) {
  return s?.allowed === true &&
    s.transport === "smtp" &&
    s.sender &&
    s.recipient &&
    ["starttls", "implicit"].includes(s.tlsMode ?? "") &&
    /^[a-f0-9]{64}$/.test(s.sourceFingerprint ?? "")
    ? s.sourceFingerprint!
    : null;
}
function validRow(r: AlertMailTest, event: string) {
  return (
    !!r &&
    typeof r === "object" &&
    guid(r.id) &&
    guid(r.requestId) &&
    Number.isSafeInteger(r.version) &&
    r.version > 0 &&
    r.eventKind === event &&
    r.transport === "smtp" &&
    r.deliveryObserved === false
  );
}
function load(key: string, event: InstallMailEvent): Intent | null {
  const raw = sessionStorage.getItem(key);
  if (!raw) return null;
  const v = JSON.parse(raw) as Partial<Intent>;
  if (
    v.format !== 1 ||
    !guid(v.requestId) ||
    !/^[a-f0-9]{64}$/.test(v.sourceFingerprint ?? "") ||
    v.event !== event
  )
    throw new Error("Invalid recovery metadata");
  return v as Intent;
}
/** Reads are current. An uncertain send is recovered only from own-caller history. */
export function useInstallAlertMail(
  actor: string,
  org: string,
  event: InstallMailEvent,
  onEvent: (e: InstallMailEvent) => void
): InstallAlertMailPanelProps {
  const t = useTranslations("installAlertMail"),
    client = useApolloClient();
  const key = `astrolift.install-mail.v1:${actor}:${org}:${event}`;
  const admitted = guid(actor) && guid(org);
  const q = useQuery<InstallAlertMailSupportQuery>(INSTALL_ALERT_MAIL_SUPPORT, {
    variables: { eventKind: event },
    skip: !admitted,
    fetchPolicy: "no-cache",
    context: { queryDeduplication: false },
    notifyOnNetworkStatusChange: true,
  });
  const definition = React.useMemo(
    () => ({ ...emailHistoryDefinition(t("history")), id: "install.alertMail" }),
    [t]
  );
  const list = useLocalListState(definition);
  const h = useQuery<InstallAlertMailHistoryQuery>(INSTALL_ALERT_MAIL_HISTORY, {
    variables: { eventKind: event, after: list.state.after, limit: list.state.pageSize },
    skip: !admitted,
    fetchPolicy: "no-cache",
    context: { queryDeduplication: false },
    notifyOnNetworkStatusChange: true,
    pollInterval: 15000,
  });
  const support =
    admitted && !q.loading && !q.error ? (q.data?.installAlertMailSupport ?? null) : null;
  const fp = source(support);
  const [intent, setIntent] = React.useState<Intent | null>(null),
    [reviewed, setReviewed] = React.useState<string | null>(null),
    [replyRow, setCurrent] = React.useState<AlertMailTest | null>(null),
    [message, setMessage] = React.useState<InstallAlertMailPanelProps["message"]>(null),
    [busy, setBusy] = React.useState(false),
    [ready, setReady] = React.useState(false);
  const busyRef = React.useRef(false),
    live = React.useRef(true);
  React.useEffect(() => {
    live.current = true;
    queueMicrotask(() => {
      if (!live.current) return;
      try {
        const saved = load(key, event);
        setIntent(saved);
        if (saved) setMessage("uncertain");
      } catch {
        setMessage("storageUnavailable");
      }
      setReady(true);
    });
    return () => {
      live.current = false;
    };
  }, [key, event]);
  const historyReady = admitted && !h.loading && !h.error;
  const page = historyReady ? h.data?.installAlertMailTestsPage : null;
  const bad =
    historyReady &&
    (!page || !Array.isArray(page.items) || page.items.some((r) => !validRow(r, event)));
  const recovered =
    !bad && page && intent ? page.items.find((r) => r.requestId === intent.requestId) : null;
  const current = recovered ?? replyRow;
  const { refetch: refetchSupport } = q,
    { refetch: refetchHistory } = h;
  const refresh = () => {
    setReviewed(null);
    void q.refetch().catch(() => {});
    list.setPageSize(list.state.pageSize);
    void h.refetch({ after: null }).catch(() => {});
  };
  React.useEffect(() => {
    const focus = () => {
      setReviewed(null);
      void refetchSupport().catch(() => {});
      void refetchHistory().catch(() => {});
    };
    window.addEventListener("focus", focus);
    return () => window.removeEventListener("focus", focus);
  }, [refetchSupport, refetchHistory]);
  async function freshSource() {
    const result = await client.query<InstallAlertMailSupportQuery>({
      query: INSTALL_ALERT_MAIL_SUPPORT,
      variables: { eventKind: event },
      fetchPolicy: "no-cache",
      context: { queryDeduplication: false },
    });
    return source(result.data?.installAlertMailSupport);
  }
  const blocked = !ready || !admitted || message === "storageUnavailable";
  async function review() {
    if (blocked || intent || busyRef.current || !fp) return;
    busyRef.current = true;
    setBusy(true);
    try {
      const fresh = await freshSource();
      if (!live.current) return;
      if (fresh === fp) {
        setReviewed(fp);
        setMessage(null);
      } else {
        setReviewed(null);
        setMessage("sourceChanged");
        void q.refetch().catch(() => {});
      }
    } catch {
      if (live.current) {
        setReviewed(null);
        setMessage("sourceChanged");
      }
    } finally {
      busyRef.current = false;
      if (live.current) setBusy(false);
    }
  }
  async function send() {
    if (blocked || intent || busyRef.current || !fp || reviewed !== fp) return;
    busyRef.current = true;
    setBusy(true);
    try {
      const fresh = await freshSource();
      if (!live.current) return;
      if (fresh !== fp) {
        setReviewed(null);
        setMessage("sourceChanged");
        void q.refetch().catch(() => {});
        return;
      }
      const next: Intent = {
        format: 1,
        requestId: crypto.randomUUID(),
        sourceFingerprint: fp,
        event,
      };
      try {
        sessionStorage.setItem(key, JSON.stringify(next));
      } catch {
        setMessage("storageUnavailable");
        return;
      }
      setIntent(next);
      setReviewed(null);
      try {
        const result = await client.mutate<SendInstallAlertMailTestMutation>({
          mutation: SEND_INSTALL_ALERT_MAIL_TEST,
          variables: {
            input: {
              requestId: next.requestId,
              expectedSourceFingerprint: next.sourceFingerprint,
              eventKind: event,
            },
          },
          fetchPolicy: "no-cache",
        });
        if (!live.current) return;
        const reply = result.data?.sendInstallAlertMailTest,
          r = reply?.data;
        if (
          reply?.ok === true &&
          Array.isArray(reply.errors) &&
          reply.errors.length === 0 &&
          r &&
          validRow(r, event) &&
          r.requestId === next.requestId &&
          r.sender === support?.sender &&
          r.recipient === support?.recipient
        ) {
          setCurrent(r);
          setMessage(null);
        } else setMessage(reply?.ok === true ? "recordUnverified" : "uncertain");
      } catch {
        if (live.current) setMessage("uncertain");
      }
      if (live.current) void h.refetch({ after: null }).catch(() => {});
    } catch {
      if (live.current) {
        setReviewed(null);
        setMessage("sourceChanged");
      }
    } finally {
      busyRef.current = false;
      if (live.current) setBusy(false);
    }
  }
  const terminal = !!current && ["accepted", "failed"].includes(current.status);
  return {
    event,
    onEvent,
    support,
    loading: q.loading,
    supportError: !!q.error || !admitted,
    reviewed: !!fp && reviewed === fp && !q.loading && !q.error,
    busy,
    locked: !!intent || blocked,
    canReview: !!fp && !intent && !blocked,
    canSend: !!fp && reviewed === fp && !intent && !blocked,
    requestId: intent?.requestId ?? null,
    current: !h.error && !bad ? current : null,
    message: recovered ? null : message,
    canStartAnother: terminal && !h.loading && !h.error && !bad && !blocked,
    onReview: () => {
      void review();
    },
    onSend: () => {
      void send();
    },
    onRefresh: refresh,
    onStartAnother: () => {
      if (!terminal || h.loading || h.error || bad || busyRef.current) return;
      try {
        sessionStorage.removeItem(key);
      } catch {
        setMessage("storageUnavailable");
        return;
      }
      setIntent(null);
      setCurrent(null);
      setReviewed(null);
      setMessage(null);
      refresh();
    },
    history: {
      list,
      rows: bad ? [] : (page?.items ?? []),
      loading: h.loading,
      stale: false,
      error: h.error || bad ? { message: t("historyUnavailable") } : null,
      totalCount: page?.totalCount ?? null,
      nextCursor: page?.nextCursor ?? null,
      refetch: () => {
        list.setPageSize(list.state.pageSize);
        void h.refetch({ after: null }).catch(() => {});
      },
    },
  };
}
