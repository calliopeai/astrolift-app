"use client";

import { useApolloClient, useQuery } from "@apollo/client/react";
import { useState, useEffect, useRef } from "react";
import { useTranslations } from "next-intl";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { GET_ME } from "@/graphql/user/user.queries";
import type { MeQueryData } from "@/graphql/user/user.types";
import {
  REVIEW_PIPELINE_START,
  REVIEW_WORKFLOW_START,
  RECONCILE_PIPELINE_START,
  RECONCILE_WORKFLOW_START,
} from "@/graphql/reviewed-starts/reviewed.queries";
import {
  START_REVIEWED_PIPELINE,
  START_REVIEWED_WORKFLOW,
} from "@/graphql/reviewed-starts/reviewed.mutations";
import { ReviewedStartSheet } from "./ReviewedStartSheet";
import {
  readStamp,
  stampKey,
  type InputContract,
  type StartKind,
  type StartReceipt,
  type StartReview,
  type StartStamp,
} from "./reviewed-start-model";

type WorkflowReviewResponse = {
  workflowDefinitionById: {
    guid: string;
    revision: string;
    definition: { name: string; slug: string; isEnabled: boolean; stageCount: number };
    inputContract: InputContract;
  } | null;
};
type PipelineReviewResponse = {
  astroliftPipeline: { id: string; name: string; version: number; defaultBranch: string } | null;
};
type Envelope = {
  ok: boolean;
  errors: { code: string; message: string }[];
  data: StartReceipt | null;
};

/** Caller-owned request identity survives lost responses without storing submitted inputs. */
export function useReviewedStart(kind: StartKind) {
  const client = useApolloClient();
  const t = useTranslations(
    kind === "workflow" ? "ReviewedWorkflowStart" : "ReviewedPipelineStart"
  );
  const { org } = useActiveOrg();
  const me = useQuery<MeQueryData>(GET_ME).data?.me;
  const epoch = useRef(0);
  const [opened, setOpened] = useState(false);
  const [target, setTarget] = useState<string | null>(null);
  const [review, setReview] = useState<StartReview | null>(null);
  const [stamp, setStamp] = useState<StartStamp | null>(null);
  const [receipt, setReceipt] = useState<StartReceipt | null>(null);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [restored, setRestored] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [originalInputs, setOriginalInputs] = useState<Record<string, unknown> | null>(null);
  useEffect(() => {
    epoch.current += 1;
    setLoading(false);
    setSubmitting(false);
    setOpened(false);
    setTarget(null);
    setReview(null);
    setStamp(null);
    setReceipt(null);
    setOriginalInputs(null);
  }, [org?.id, me?.id]);
  const storageKey = (id: string) => (org?.id && me?.id ? stampKey(kind, org.id, me.id, id) : null);
  function persist(id: string, value: StartStamp | null) {
    const key = storageKey(id);
    if (!key) return;
    try {
      if (value) sessionStorage.setItem(key, JSON.stringify(value));
      else sessionStorage.removeItem(key);
    } catch {
      /* memory identity still applies */
    }
  }
  async function reconcile(id = target, saved = stamp) {
    if (!id || !saved) return;
    const requestEpoch = epoch.current;
    setSubmitting(true);
    setError(null);
    try {
      const variables =
        kind === "workflow"
          ? { requestId: saved.requestId }
          : { pipelineId: id, requestId: saved.requestId };
      const response = await client.query<Record<string, StartReceipt | null>>({
        query: kind === "workflow" ? RECONCILE_WORKFLOW_START : RECONCILE_PIPELINE_START,
        variables,
        fetchPolicy: "no-cache",
      });
      if (requestEpoch !== epoch.current) return;
      const found =
        response.data?.[
          kind === "workflow" ? "workflowDefinitionStartRequest" : "pipelineStartRequest"
        ] ?? null;
      setReceipt(found);
      setUncertain(!found || found.dispatchStatus !== "submitted");
      if (!found) setError(t("notFound"));
    } catch {
      if (requestEpoch === epoch.current) {
        setError(t("unavailable"));
        setUncertain(true);
      }
    } finally {
      if (requestEpoch === epoch.current) setSubmitting(false);
    }
  }
  async function open(id: string) {
    if (!org?.id || !me?.id) {
      setError(t("identityRequired"));
      return;
    }
    if (submitting) return;
    const requestEpoch = ++epoch.current;
    // Reopening an uncertain in-memory start preserves its exact original body.
    const same = target === id && stamp !== null;
    setOpened(true);
    setTarget(id);
    setLoading(true);
    setReview(null);
    setError(null);
    let saved = same ? stamp : null;
    if (!same) {
      try {
        saved = readStamp(sessionStorage.getItem(storageKey(id)!), kind, id);
      } catch {
        saved = null;
      }
      setOriginalInputs(null);
      setReceipt(null);
    }
    setStamp(saved);
    setRestored(Boolean(saved) && !same);
    setUncertain(Boolean(saved));
    try {
      if (kind === "workflow") {
        const response = await client.query<WorkflowReviewResponse>({
          query: REVIEW_WORKFLOW_START,
          variables: { id },
          fetchPolicy: "no-cache",
        });
        if (requestEpoch !== epoch.current) return;
        const value = response.data?.workflowDefinitionById;
        if (!value) throw new Error("missing");
        setReview({
          id: value.guid,
          name: value.definition.name,
          revision: value.revision,
          enabled: value.definition.isEnabled && value.definition.stageCount > 0,
          contract: value.inputContract,
        });
      } else {
        const response = await client.query<PipelineReviewResponse>({
          query: REVIEW_PIPELINE_START,
          variables: { id },
          fetchPolicy: "no-cache",
        });
        if (requestEpoch !== epoch.current) return;
        const value = response.data?.astroliftPipeline;
        if (!value) throw new Error("missing");
        setReview({
          id: value.id,
          name: value.name,
          revision: value.version,
          enabled: true,
          defaultBranch: value.defaultBranch,
        });
      }
      if (saved) await reconcile(id, saved);
    } catch {
      if (requestEpoch === epoch.current) setError(t("unavailable"));
    } finally {
      if (requestEpoch === epoch.current) setLoading(false);
    }
  }
  async function submit(inputs: Record<string, unknown>, ref: string) {
    if (!review || !target || submitting) return;
    const saved: StartStamp = stamp ?? {
      kind,
      targetId: target,
      requestId: crypto.randomUUID(),
      revision: review.revision,
      ...(review.contract ? { inputSchemaDigest: review.contract.digest } : {}),
      ...(kind === "pipeline" ? { ref } : {}),
    };
    const requestEpoch = epoch.current;
    const payload = originalInputs ?? inputs;
    setStamp(saved);
    setOriginalInputs(payload);
    persist(target, saved);
    setSubmitting(true);
    setError(null);
    try {
      const input =
        kind === "workflow"
          ? {
              definitionId: target,
              expectedRevision: saved.revision,
              expectedInputSchemaDigest: saved.inputSchemaDigest,
              requestId: saved.requestId,
              inputs: payload,
              confirmed: true,
            }
          : {
              pipelineId: target,
              expectedVersion: saved.revision,
              requestId: saved.requestId,
              ref: saved.ref,
              confirmed: true,
            };
      const response = await client.mutate<Record<string, Envelope>>({
        mutation: kind === "workflow" ? START_REVIEWED_WORKFLOW : START_REVIEWED_PIPELINE,
        variables: { input },
      });
      if (requestEpoch !== epoch.current) return;
      const envelope =
        response.data?.[kind === "workflow" ? "startWorkflowDefinition" : "startPipelineRun"];
      setReceipt(envelope?.data ?? null);
      setUncertain(!envelope?.ok);
      if (!envelope?.ok)
        setError(
          envelope?.errors.map((e) => `${e.code}: ${e.message}`).join(" · ") || t("unavailable")
        );
    } catch {
      if (requestEpoch === epoch.current) {
        setError(t("unavailable"));
        setUncertain(true);
      }
    } finally {
      if (requestEpoch === epoch.current) setSubmitting(false);
    }
  }
  function close() {
    if (submitting) return;
    if (target && receipt?.dispatchStatus === "submitted" && receipt.temporalRunId) {
      persist(target, null);
      setStamp(null);
      setOriginalInputs(null);
    }
    epoch.current += 1;
    setLoading(false);
    setOpened(false);
  }
  const dialog =
    opened && target ? (
      <ReviewedStartSheet
        key={`${target}:${review?.revision ?? "loading"}`}
        kind={kind}
        open
        loading={loading}
        submitting={submitting}
        review={review}
        stamp={stamp}
        receipt={receipt}
        error={error}
        uncertain={uncertain}
        restored={restored}
        onClose={close}
        onReconcile={() => reconcile()}
        onSubmit={submit}
      />
    ) : null;
  return { open, dialog, busy: loading || submitting };
}
