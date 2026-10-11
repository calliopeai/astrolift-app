"use client";

import * as React from "react";
import { useApolloClient, useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { useLocalListState } from "@/components/list/use-list-state";
import { deliveryValidation } from "./email-delivery-validation";
import { emailHistoryDefinition } from "./email-delivery-history";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { useMe } from "@/graphql/user/user.hooks";
import {
  EMAIL_DELIVERY_SUPPORT,
  EMAIL_DELIVERY_TESTS_PAGE,
  SEND_EMAIL_DELIVERY_TEST,
} from "@/graphql/services/email-delivery.queries";
import type {
  EmailDeliverySupportQuery,
  EmailDeliverySupportQueryVariables,
  EmailDeliveryTestsPageQuery,
  EmailDeliveryTestsPageQueryVariables,
  SendEmailDeliveryTestMutation,
  SendEmailDeliveryTestMutationVariables,
} from "@/graphql/__generated__/operations";
import type {
  DeliveryDraft,
  DeliverySupport,
  DeliveryTest,
  EmailDeliveryPanelProps,
} from "./EmailDeliveryPanel";

type Source = {
  serviceVersion: number;
  sender: string;
  identity: string;
  accountId: string;
  region: string;
};
type Intent = { format: 1; requestId: string; contentSha256: string; source: Source };
const guid = (value: unknown): value is string =>
  typeof value === "string" && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(value);
const blank: DeliveryDraft = { recipient: "", subject: "", body: "" };
function source(value: DeliverySupport | null): Source | null {
  if (
    value?.allowed !== true ||
    !Number.isSafeInteger(value.serviceVersion) ||
    (value.serviceVersion ?? 0) < 1 ||
    !value.sender ||
    !value.identity ||
    !/^\d{12}$/.test(value.accountId ?? "") ||
    !value.region
  )
    return null;
  return {
    serviceVersion: value.serviceVersion!,
    sender: value.sender,
    identity: value.identity,
    accountId: value.accountId!,
    region: value.region,
  };
}
function readIntent(key: string): Intent | null {
  const raw = sessionStorage.getItem(key);
  if (!raw) return null;
  const value = JSON.parse(raw) as Partial<Intent>;
  if (
    value.format !== 1 ||
    !guid(value.requestId) ||
    !/^[a-f0-9]{64}$/.test(value.contentSha256 ?? "") ||
    !source({ allowed: true, ...value.source })
  )
    throw new Error("Invalid recovery metadata");
  return value as Intent;
}
async function contentSha(draft: DeliveryDraft) {
  const bytes = new TextEncoder().encode(
    JSON.stringify([draft.recipient.trim(), draft.subject.trim(), draft.body.trim()])
  );
  return [...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))]
    .map((v) => v.toString(16).padStart(2, "0"))
    .join("");
}
const normalize = (draft: DeliveryDraft): DeliveryDraft => ({
  recipient: draft.recipient.trim(),
  subject: draft.subject.trim(),
  body: draft.body.trim(),
});
function correlated(row: DeliveryTest, id: string, intent: Intent, draft: DeliveryDraft) {
  return (
    guid(row.id) &&
    row.managedServiceId === id &&
    row.requestId === intent.requestId &&
    row.recipient === draft.recipient.trim() &&
    row.sender === intent.source.sender &&
    row.identity === intent.source.identity &&
    row.accountId === intent.source.accountId &&
    row.region === intent.source.region &&
    Number.isSafeInteger(row.version) &&
    row.version > 0
  );
}
/** Current actor-bound reads, reviewed source and a content-free stable request nonce. */
export function useEmailDelivery(id: string, serviceName: string): EmailDeliveryPanelProps {
  const t = useTranslations("emailDelivery"),
    client = useApolloClient();
  const { org, loading: orgLoading, error: orgError } = useActiveOrg(),
    { user, loading: userLoading, error: userError } = useMe();
  const scope = JSON.stringify([user?.id, org?.id, id]);
  const admitted =
    guid(id) && !!org?.id && !!user?.id && !orgLoading && !userLoading && !orgError && !userError;
  const query = useQuery<EmailDeliverySupportQuery, EmailDeliverySupportQueryVariables>(
    EMAIL_DELIVERY_SUPPORT,
    {
      variables: { managedServiceId: id },
      skip: !admitted,
      fetchPolicy: "no-cache",
      context: { queryDeduplication: false },
    }
  );
  const support =
    !admitted || query.loading || query.error
      ? null
      : (query.data?.emailDeliveryTestSupport ?? null);
  const observedSource = source(support);
  const currentSource = observedSource ? JSON.stringify(observedSource) : null;
  const listDefinition = React.useMemo(() => emailHistoryDefinition(t("history")), [t]);
  const list = useLocalListState(listDefinition);
  const historyQuery = useQuery<EmailDeliveryTestsPageQuery, EmailDeliveryTestsPageQueryVariables>(
    EMAIL_DELIVERY_TESTS_PAGE,
    {
      variables: { managedServiceId: id, after: list.state.after, limit: list.state.pageSize },
      skip: !admitted,
      fetchPolicy: "no-cache",
      context: { queryDeduplication: false },
      notifyOnNetworkStatusChange: true,
      pollInterval: 15000,
    }
  );
  const historyPage =
    !admitted || historyQuery.error ? null : historyQuery.data?.emailDeliveryTestsPage;
  const badHistory = historyPage?.items.some(
    (row) =>
      row.managedServiceId !== id ||
      !guid(row.id) ||
      !guid(row.requestId) ||
      !Number.isSafeInteger(row.version) ||
      row.version < 1
  );
  const history = {
    list,
    rows: badHistory ? [] : (historyPage?.items ?? []),
    loading: historyQuery.loading && !historyPage,
    stale: historyQuery.loading && !!historyPage,
    error: historyQuery.error || badHistory ? { message: t("historyUnavailable") } : null,
    totalCount: historyPage?.totalCount ?? null,
    nextCursor: historyPage?.nextCursor ?? null,
    refetch: () => {
      list.setPageSize(list.state.pageSize);
      void historyQuery.refetch({ after: null }).catch(() => {});
    },
  };
  const [draft, setDraft] = React.useState<DeliveryDraft>(blank);
  const [intent, setIntent] = React.useState<Intent | null>(null);
  const [recovered, setRecovered] = React.useState(false);
  const [storageReady, setStorageReady] = React.useState(false);
  const [reviewedSnapshot, setReviewedSnapshot] = React.useState<string | null>(null);
  const [reply, setReply] = React.useState<DeliveryTest | null>(null);
  const [message, setMessage] = React.useState<EmailDeliveryPanelProps["message"]>(null);
  const [actionError, setActionError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);
  const busyRef = React.useRef(false),
    mounted = React.useRef(false),
    scopeRef = React.useRef(scope);
  const storageKey = `astrolift.email-test.v1:${scope}`;
  React.useLayoutEffect(() => {
    scopeRef.current = scope;
  });
  React.useLayoutEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  React.useEffect(() => {
    if (!admitted) return;
    try {
      const saved = readIntent(storageKey);
      if (saved) {
        // eslint-disable-next-line react-hooks/set-state-in-effect -- Recovery reads sessionStorage after mount to preserve hydration; message content is never stored.
        setIntent(saved);
        setRecovered(true);
        setMessage("uncertain");
      }
    } catch {
      setActionError(t("uncertain"));
      return;
    }
    setStorageReady(true);
  }, [admitted, storageKey, t]);
  const matchedHistory = intent
    ? history.rows.find((row) => row.requestId === intent.requestId)
    : null;
  const current =
    intent &&
    matchedHistory &&
    guid(matchedHistory.id) &&
    matchedHistory.managedServiceId === id &&
    matchedHistory.sender === intent.source.sender &&
    matchedHistory.accountId === intent.source.accountId &&
    matchedHistory.region === intent.source.region &&
    matchedHistory.identity === intent.source.identity
      ? matchedHistory
      : reply;
  const draftKey = JSON.stringify(normalize(draft));
  const reviewKey = JSON.stringify([currentSource, draftKey]);
  const reviewed = !!currentSource && reviewedSnapshot === reviewKey;
  const sourceSame = !intent || JSON.stringify(intent.source) === currentSource;
  const validation = deliveryValidation(draft);
  const hasRecipient = validation === null;
  const canReview =
    admitted && storageReady && !!currentSource && sourceSame && hasRecipient && !busy;
  const canSend =
    admitted &&
    storageReady &&
    !!currentSource &&
    sourceSame &&
    reviewed &&
    hasRecipient &&
    !busy &&
    !current;
  const latest = React.useRef({ scope, reviewKey, canReview, canSend, currentSource });
  React.useLayoutEffect(() => {
    latest.current = { scope, reviewKey, canReview, canSend, currentSource };
  });
  function stillCurrent(captured: string) {
    return mounted.current && scopeRef.current === captured;
  }
  function onRefresh() {
    setReviewedSnapshot(null);
    if (admitted) {
      void query.refetch().catch(() => {});
      history.refetch();
    }
  }
  function onDraft(next: DeliveryDraft) {
    if (busyRef.current || (intent && !recovered)) return;
    setDraft(next);
    setReviewedSnapshot(null);
    setActionError(null);
  }
  async function onReview() {
    if (!latest.current.canReview || busyRef.current) return;
    const captured = scope,
      key = reviewKey;
    try {
      const hash = await contentSha(draft);
      if (!stillCurrent(captured) || latest.current.reviewKey !== key) return;
      if (intent && hash !== intent.contentSha256) {
        setMessage("uncertain");
        return;
      }
      setReviewedSnapshot(key);
      setActionError(null);
    } catch {
      if (stillCurrent(captured)) setActionError(t("uncertain"));
    }
  }
  async function onSend() {
    if (!latest.current.canSend || busyRef.current || !observedSource || deliveryValidation(draft))
      return;
    const captured = scope,
      key = reviewKey,
      values = normalize(draft);
    busyRef.current = true;
    setBusy(true);
    setActionError(null);
    try {
      const hash = await contentSha(values);
      if (!stillCurrent(captured) || latest.current.reviewKey !== key) return;
      const submitted = intent ?? {
        format: 1 as const,
        requestId: crypto.randomUUID(),
        contentSha256: hash,
        source: observedSource,
      };
      if (
        submitted.contentSha256 !== hash ||
        !guid(submitted.requestId) ||
        JSON.stringify(submitted.source) !== currentSource
      )
        return;
      // Commit the content-free replay identity before dispatch; storage failure refuses a new send.
      sessionStorage.setItem(storageKey, JSON.stringify(submitted));
      setIntent(submitted);
      setRecovered(false);
      const response = await client.mutate<
        SendEmailDeliveryTestMutation,
        SendEmailDeliveryTestMutationVariables
      >({
        mutation: SEND_EMAIL_DELIVERY_TEST,
        variables: {
          input: {
            managedServiceId: id,
            expectedVersion: submitted.source.serviceVersion,
            requestId: submitted.requestId,
            recipient: values.recipient,
            subject: values.subject || null,
            body: values.body || null,
          },
        },
        fetchPolicy: "no-cache",
      });
      if (!stillCurrent(captured)) return;
      const envelope = response.data?.sendEmailDeliveryTest;
      if (envelope?.ok === true) {
        if (envelope.data && correlated(envelope.data, id, submitted, values)) {
          setReply(envelope.data);
          setMessage(null);
        } else setMessage("acceptedUnverified");
      } else {
        // A current-source refusal can follow an accepted send. Keep the replay key and recover history.
        setMessage("uncertain");
        if (envelope?.ok === false && envelope.errors[0]?.message)
          setActionError(envelope.errors[0].message);
      }
      history.refetch();
    } catch {
      if (stillCurrent(captured)) setMessage("uncertain");
    } finally {
      busyRef.current = false;
      if (stillCurrent(captured)) setBusy(false);
    }
  }
  const canStartAnother =
    !!current &&
    !busy &&
    !history.error &&
    !!currentSource &&
    [
      "accepted",
      "failed",
      "suppressed",
      "delivered",
      "deferred",
      "bounced",
      "complained",
      "rejected",
      "observation_timed_out",
    ].includes(current.status);
  function onStartAnother() {
    if (!canStartAnother) return;
    try {
      sessionStorage.removeItem(storageKey);
    } catch {
      setActionError(t("uncertain"));
      return;
    }
    setIntent(null);
    setReply(null);
    setRecovered(false);
    setReviewedSnapshot(null);
    setDraft(blank);
    setMessage(null);
    setActionError(null);
  }
  return {
    serviceName,
    support,
    supportLoading: orgLoading || userLoading || query.loading,
    supportError: orgError || userError || query.error ? t("supportUnavailable") : null,
    draft,
    onDraft,
    reviewed,
    canReview,
    canSend,
    busy,
    locked: !!intent && !recovered,
    recovered,
    requestId: intent?.requestId ?? null,
    current,
    message,
    actionError: actionError ?? (draft.recipient && validation ? t(validation) : null),
    canStartAnother,
    onReview: () => {
      void onReview();
    },
    onSend: () => {
      void onSend();
    },
    onStartAnother,
    onRefresh,
    history,
  };
}
