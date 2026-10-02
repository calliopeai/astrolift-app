"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import {
  initialFieldValues,
  simpleInputs,
  type StartKind,
  type StartReceipt,
  type StartReview,
  type StartStamp,
} from "./reviewed-start-model";

export interface ReviewedStartSheetProps {
  kind: StartKind;
  open: boolean;
  loading: boolean;
  submitting: boolean;
  review: StartReview | null;
  stamp: StartStamp | null;
  receipt: StartReceipt | null;
  error: string | null;
  uncertain: boolean;
  restored: boolean;
  onClose: () => void;
  onReconcile: () => Promise<void>;
  onSubmit: (inputs: Record<string, unknown>, ref: string) => Promise<void>;
}

/** Review and explicit confirmation are shared by definition and pipeline starts. */
export function ReviewedStartSheet(props: ReviewedStartSheetProps) {
  const t = useTranslations(
    props.kind === "workflow" ? "ReviewedWorkflowStart" : "ReviewedPipelineStart"
  );
  const { review, stamp, receipt } = props;
  const contract = review?.contract;
  const [confirmed, setConfirmed] = useState(false);
  const [values, setValues] = useState(() => (contract ? initialFieldValues(contract) : {}));
  const [json, setJson] = useState("{}");
  const [ref, setRef] = useState(stamp?.ref ?? review?.defaultBranch ?? "");
  const [inputError, setInputError] = useState(false);
  const submitted = receipt?.dispatchStatus === "submitted" && Boolean(receipt.temporalRunId);
  const stale =
    stamp !== null &&
    (stamp.revision !== review?.revision ||
      (contract !== undefined && stamp.inputSchemaDigest !== contract.digest));
  const unavailable = !review || !review.enabled || contract?.supported === false || stale;
  return (
    <Sheet
      open={props.open}
      onOpenChange={(open) => {
        if (!open) props.onClose();
      }}
    >
      <SheetContent showCloseButton={false} className="flex flex-col sm:max-w-xl">
        <SheetHeader>
          <SheetTitle>{t("title")}</SheetTitle>
          <SheetDescription>{t("description")}</SheetDescription>
        </SheetHeader>
        <div className="flex-1 space-y-4 overflow-y-auto px-4 pb-4">
          {props.loading && <p role="status">{t("loading")}</p>}
          {review && (
            <dl className="space-y-2 text-xs break-all">
              <div>
                <dt className="text-muted-foreground">{t("target")}</dt>
                <dd>
                  {review.name} · {review.id}
                </dd>
              </div>
              <div>
                <dt className="text-muted-foreground">{t("revision")}</dt>
                <dd className="font-mono">{stamp?.revision ?? review.revision}</dd>
              </div>
              {contract && (
                <div>
                  <dt className="text-muted-foreground">{t("schema")}</dt>
                  <dd className="font-mono">{stamp?.inputSchemaDigest ?? contract.digest}</dd>
                </div>
              )}
            </dl>
          )}
          {props.error && (
            <p role="alert" className="text-destructive">
              {props.error}
            </p>
          )}
          {stale && <p role="alert">{t("stale")}</p>}
          {review && !review.enabled && <p role="alert">{t("disabled")}</p>}
          {contract?.supported === false && (
            <p role="alert">
              {t("unsupported")} {contract.error}
            </p>
          )}
          {props.uncertain && <p role="status">{t("uncertain")}</p>}
          {props.restored && !submitted && <p>{t("restored")}</p>}
          {stamp && (
            <p className="text-xs break-all">
              {t("requestId")}: <span className="font-mono">{stamp.requestId}</span>
            </p>
          )}
          {receipt && (
            <dl className="space-y-1 text-xs break-all">
              <div>
                <dt>{t("execution")}</dt>
                <dd className="font-mono">{receipt.id}</dd>
              </div>
              <div>
                <dt>{t("engine")}</dt>
                <dd className="font-mono">{receipt.temporalWorkflowId}</dd>
              </div>
              <div>
                <dt>{t("engineRun")}</dt>
                <dd className="font-mono">{receipt.temporalRunId ?? t("pending")}</dd>
              </div>
            </dl>
          )}
          {submitted && <p role="status">{t("submitted")}</p>}
          {!submitted && review && (
            <>
              {props.kind === "pipeline" && (
                <div className="space-y-2">
                  <Label htmlFor="reviewed-pipeline-ref">{t("branch")}</Label>
                  <Input
                    id="reviewed-pipeline-ref"
                    value={ref}
                    disabled={props.submitting || Boolean(stamp)}
                    onChange={(e) => setRef(e.target.value)}
                  />
                </div>
              )}
              {contract && !contract.acceptsInputs && <p>{t("noInputs")}</p>}
              {contract?.supported &&
                contract.acceptsInputs &&
                contract.supportsSimpleForm &&
                contract.fields.map((field) => (
                  <div className="space-y-2" key={field.name}>
                    <Label htmlFor={`reviewed-input-${field.name}`}>
                      {field.name}
                      {field.required ? ` · ${t("required")}` : ""}
                    </Label>
                    {field.enumValues || field.kind === "boolean" ? (
                      <select
                        className="border-input bg-background h-9 w-full rounded-md border px-3"
                        id={`reviewed-input-${field.name}`}
                        value={
                          values[field.name] === undefined
                            ? "unset"
                            : String(
                                (field.enumValues ?? [true, false]).findIndex(
                                  (value) => String(value) === values[field.name]
                                )
                              )
                        }
                        disabled={props.submitting}
                        onChange={(e) =>
                          setValues((v) => ({
                            ...v,
                            [field.name]:
                              e.target.value === "unset"
                                ? undefined
                                : String(
                                    (field.enumValues ?? [true, false])[Number(e.target.value)]
                                  ),
                          }))
                        }
                      >
                        <option value="unset">{t("unset")}</option>
                        {(field.enumValues ?? [true, false]).map((value, index) => (
                          <option key={JSON.stringify(value)} value={index}>
                            {String(value)}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <Input
                        id={`reviewed-input-${field.name}`}
                        type={
                          field.sensitive
                            ? "password"
                            : ["integer", "number"].includes(field.kind)
                              ? "number"
                              : "text"
                        }
                        autoComplete="off"
                        value={values[field.name] ?? ""}
                        disabled={props.submitting}
                        onChange={(e) =>
                          setValues((v) => ({
                            ...v,
                            [field.name]:
                              e.target.value === "" && field.kind !== "string"
                                ? undefined
                                : e.target.value,
                          }))
                        }
                      />
                    )}
                    {field.sensitive && (
                      <p className="text-muted-foreground text-xs">{t("secretReference")}</p>
                    )}
                    {Object.keys(field.constraints).length > 0 && (
                      <p className="text-muted-foreground text-xs break-all">
                        {t("constraints")}: {JSON.stringify(field.constraints)}
                      </p>
                    )}
                  </div>
                ))}
              {contract?.supported && contract.acceptsInputs && !contract.supportsSimpleForm && (
                <div className="space-y-2">
                  <Label htmlFor="reviewed-input-json">{t("jsonInputs")}</Label>
                  <Textarea
                    id="reviewed-input-json"
                    className="min-h-32 font-mono"
                    value={json}
                    disabled={props.submitting}
                    onChange={(e) => setJson(e.target.value)}
                  />
                  <details>
                    <summary>{t("schema")}</summary>
                    <pre className="overflow-auto text-xs">
                      {JSON.stringify(contract.schema, null, 2)}
                    </pre>
                  </details>
                </div>
              )}
              {inputError && <p role="alert">{t("invalidInputs")}</p>}
              <Label className="flex items-start gap-2">
                <input
                  type="checkbox"
                  checked={confirmed}
                  disabled={props.submitting || unavailable}
                  onChange={(e) => setConfirmed(e.target.checked)}
                />
                {t("confirm")}
              </Label>
            </>
          )}
        </div>
        <SheetFooter className="flex-row justify-end gap-2">
          <Button variant="outline" onClick={props.onClose} disabled={props.submitting}>
            {t("close")}
          </Button>
          {stamp && (
            <Button
              variant="outline"
              onClick={() => void props.onReconcile()}
              disabled={props.submitting}
            >
              {t("reconcile")}
            </Button>
          )}
          {!submitted && (
            <Button
              disabled={!confirmed || unavailable || props.loading || props.submitting}
              onClick={async () => {
                setInputError(false);
                let inputs: Record<string, unknown> = {};
                try {
                  if (contract?.acceptsInputs) {
                    inputs = contract.supportsSimpleForm
                      ? simpleInputs(contract, values)
                      : JSON.parse(json);
                    if (!inputs || Array.isArray(inputs) || typeof inputs !== "object")
                      throw new Error("object");
                  }
                } catch {
                  setInputError(true);
                  return;
                }
                await props.onSubmit(inputs, ref);
              }}
            >
              {props.submitting ? t("submitting") : stamp ? t("retry") : t("start")}
            </Button>
          )}
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
