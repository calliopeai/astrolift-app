"use client";
import { useId, useLayoutEffect, useRef, useState } from "react";
import { useFormatter, useTranslations } from "next-intl";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { QueryError } from "@/components/QueryError";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import type { ModelConnectionPolicyFieldsFragment } from "@/graphql/__generated__/operations";
export type ConnectionPolicyDraft = {
  mode: "AUTO" | "REQUIRE_APPROVAL" | "DENY";
  requiredApprovals: number;
  allowSelfApproval: boolean;
};
export type ConnectionPolicyReview = {
  policy: ModelConnectionPolicyFieldsFragment;
  draft: ConnectionPolicyDraft;
};
export type ConnectionPolicyResult =
  | { accepted: true; policy: ModelConnectionPolicyFieldsFragment | null }
  | { accepted: false; message: string };
export type ModelConnectionPolicyProps = {
  scopeKey: string;
  restriction: boolean;
  policy: ModelConnectionPolicyFieldsFragment | null;
  loading: boolean;
  stale: boolean;
  error: string | null;
  supported: boolean | null;
  supportError: string | null;
  canEdit: boolean;
  onRetry: () => void;
  onRetrySupport: () => void;
  onSubmit: (input: ConnectionPolicyReview) => Promise<ConnectionPolicyResult>;
  onAccepted: (policy: ModelConnectionPolicyFieldsFragment | null) => Promise<unknown>;
};
export function ModelConnectionPolicyPanel(props: ModelConnectionPolicyProps) {
  return <Policy key={props.scopeKey} {...props} />;
}
function Policy(props: ModelConnectionPolicyProps) {
  const t = useTranslations("models.shared.connections"),
    id = useId(),
    format = useFormatter();
  const initial = (policy: ModelConnectionPolicyFieldsFragment | null) => ({
    mode: policy?.mode ?? "AUTO",
    quorum: policy ? String(policy.requiredApprovals) : "",
    self: policy?.allowSelfApproval ?? false,
  });
  const source = JSON.stringify(props.policy),
    [state, setState] = useState({ source, draft: initial(props.policy) });
  if (state.source !== source) setState({ source, draft: initial(props.policy) });
  const { draft } = state;
  const [review, setReview] = useState<{ input: ConnectionPolicyReview; revision: number } | null>(
      null
    ),
    [failure, setFailure] = useState<string | null>(null),
    [receipt, setReceipt] = useState<{ verified: boolean; refreshFailed: boolean } | null>(null);
  const snapshot = JSON.stringify([
    props.scopeKey,
    props.policy,
    props.loading,
    props.stale,
    props.error,
    props.supported,
    props.supportError,
    props.canEdit,
  ]);
  const [binding, setBinding] = useState({ key: snapshot, revision: 0 });
  if (binding.key !== snapshot) setBinding({ key: snapshot, revision: binding.revision + 1 });
  const latest = useRef({ props, revision: binding.revision }),
    mounted = useRef(false);
  useLayoutEffect(() => {
    latest.current = { props, revision: binding.revision };
  });
  useLayoutEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const valid =
    /^\d+$/.test(draft.quorum) &&
    Number(draft.quorum) >= 1 &&
    Number(draft.quorum) <= 16 &&
    ["AUTO", "REQUIRE_APPROVAL", "DENY"].includes(draft.mode);
  const ready =
    props.supported === true &&
    !props.supportError &&
    props.canEdit &&
    !props.loading &&
    !props.stale &&
    !props.error &&
    !!props.policy;
  const current = !!review && ready && review.revision === binding.revision;
  async function confirm() {
    const candidate = review;
    if (!candidate || !mounted.current || candidate.revision !== latest.current.revision)
      return false;
    setFailure(null);
    let result: ConnectionPolicyResult;
    try {
      result = await latest.current.props.onSubmit(candidate.input);
    } catch {
      if (mounted.current && candidate.revision === latest.current.revision)
        setFailure(t("uncertain"));
      return false;
    }
    if (!mounted.current || candidate.revision !== latest.current.revision) return false;
    if (!result.accepted) {
      setFailure(result.message);
      return false;
    }
    setReceipt({ verified: !!result.policy, refreshFailed: false });
    setReview(null);
    try {
      await latest.current.props.onAccepted(result.policy);
    } catch {
      if (mounted.current) setReceipt({ verified: !!result.policy, refreshFailed: true });
    }
    return true;
  }
  return (
    <section className="space-y-4" aria-labelledby={`${id}-title`}>
      <h2 id={`${id}-title`} className="text-lg font-semibold">
        {t(props.restriction ? "restrictionTitle" : "policyTitle")}
      </h2>
      <p>{t(props.restriction ? "restrictionDescription" : "policyDescription")}</p>
      {props.supported !== true ? (
        <>
          <p role={props.supportError ? "alert" : "status"}>
            {props.supportError ??
              t(props.supported === false ? "unsupported" : "capabilityChecking")}
          </p>
          <Button onClick={props.onRetrySupport}>{t("retry")}</Button>
        </>
      ) : (
        <>
          {props.error && (
            <QueryError
              title={t("requestFailed")}
              error={props.error}
              onRetry={props.onRetry}
              retryLabel={t("retry")}
            />
          )}
          {props.loading && <p role="status">{t("capabilityChecking")}</p>}
          {!props.policy && !props.loading && !props.error && <p>{t("policyUnavailable")}</p>}
          {props.policy && (
            <form
              className="space-y-4"
              onSubmit={(event) => {
                event.preventDefault();
                if (!ready || !valid || !props.policy) return;
                setFailure(null);
                setReceipt(null);
                setReview({
                  revision: binding.revision,
                  input: {
                    policy: props.policy,
                    draft: {
                      mode: draft.mode,
                      requiredApprovals: Number(draft.quorum),
                      allowSelfApproval: draft.self,
                    },
                  },
                });
              }}
            >
              <div className="space-y-2">
                <Label htmlFor={`${id}-mode`}>{t("mode")}</Label>
                <select
                  id={`${id}-mode`}
                  value={draft.mode}
                  disabled={!ready}
                  onChange={(event) => {
                    const value = event.target.value;
                    if (value === "AUTO" || value === "REQUIRE_APPROVAL" || value === "DENY")
                      setState({ ...state, draft: { ...draft, mode: value } });
                  }}
                  className="border-input bg-background rounded-md border p-2"
                >
                  {(["AUTO", "REQUIRE_APPROVAL", "DENY"] as const).map((mode) => (
                    <option key={mode} value={mode}>
                      {t(
                        mode === "AUTO" ? "auto" : mode === "REQUIRE_APPROVAL" ? "approval" : "deny"
                      )}
                    </option>
                  ))}
                </select>
              </div>
              <div className="space-y-2">
                <Label htmlFor={`${id}-quorum`}>{t("quorum")}</Label>
                <Input
                  id={`${id}-quorum`}
                  type="number"
                  min={1}
                  max={16}
                  value={draft.quorum}
                  disabled={!ready}
                  onChange={(event) =>
                    setState({ ...state, draft: { ...draft, quorum: event.target.value } })
                  }
                />
                <p>{t("quorumHelp")}</p>
                {!valid && <p role="alert">{t("quorumInvalid")}</p>}
              </div>
              <div className="flex items-center gap-2">
                <Checkbox
                  id={`${id}-self`}
                  checked={draft.self}
                  disabled={!ready}
                  onCheckedChange={(value) =>
                    setState({ ...state, draft: { ...draft, self: value === true } })
                  }
                />
                <Label htmlFor={`${id}-self`}>{t("selfApproval")}</Label>
              </div>
              <Button type="submit" disabled={!ready || !valid}>
                {t("savePolicy")}
              </Button>
            </form>
          )}
        </>
      )}
      {receipt && (
        <div role="status">
          <p>
            {t(
              !receipt.verified
                ? "acceptedUnverified"
                : props.restriction
                  ? "restrictionSaved"
                  : "policySaved"
            )}
          </p>
          {receipt.refreshFailed && <p>{t("refreshFailed")}</p>}
        </div>
      )}
      {failure && <p role="alert">{failure}</p>}
      <ConfirmDialog
        key={review?.revision ?? "closed"}
        open={!!review}
        confirmDisabled={!current}
        title={t("confirmPolicy")}
        description={
          <div className="space-y-2">
            <p>{t("reviewPolicy")}</p>
            {review && (
              <p>
                {t(
                  review.input.draft.mode === "AUTO"
                    ? "auto"
                    : review.input.draft.mode === "REQUIRE_APPROVAL"
                      ? "approval"
                      : "deny"
                )}{" "}
                · {t("quorum")}: {format.number(review.input.draft.requiredApprovals)} ·{" "}
                <span className="inline-flex items-center gap-2">
                  <Checkbox
                    aria-label={t("selfApproval")}
                    checked={review.input.draft.allowSelfApproval}
                    disabled
                  />
                  {t("selfApproval")}
                </span>
              </p>
            )}
            <p>{t("policyReviewNotice")}</p>
            {!current && <p>{t("changed")}</p>}
          </div>
        }
        confirmLabel={t("savePolicy")}
        onConfirm={confirm}
        onOpenChange={(open) => {
          if (!open) setReview((existing) => (existing === review ? null : existing));
        }}
      />
    </section>
  );
}
