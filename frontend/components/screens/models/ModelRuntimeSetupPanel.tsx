"use client";
import { useId, useState, useLayoutEffect, useRef } from "react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type {
  ModelRuntimeDeclarationInput,
  ModelRuntimeMode,
  ModelDtype,
} from "@/graphql/__generated__/operations";

export type RuntimeDeclaration = ModelRuntimeDeclarationInput;
export type RuntimeObservation = {
  organizationId: string;
  clusterId: string;
  providerId: string;
  clusterVersion: number;
  providerVersion: number;
  modes: {
    computeMode: ModelRuntimeMode;
    configured: boolean;
    reason?: string | null;
    declaration?:
      | (Omit<RuntimeDeclaration, "hardwareAttested" | "hardwareEvidence"> & {
          hardwareEvidence?: string | null;
        })
      | null;
  }[];
};
export interface ModelRuntimeSetupProps {
  scopeKey: string;
  allowed: boolean;
  mode: ModelRuntimeMode;
  observation: RuntimeObservation | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  onSave: (
    declaration: RuntimeDeclaration,
    observation: RuntimeObservation
  ) => Promise<{ accepted: boolean; message?: string; refreshFailed?: boolean }>;
}
const dtypes: ModelDtype[] = ["AUTO", "FLOAT16", "BFLOAT16", "FLOAT32"];
function defaults(mode: ModelRuntimeMode): RuntimeDeclaration {
  return {
    image: "",
    version: "0.15.1",
    packageVersion: mode === "CPU" ? "0.15.1+cpu" : "0.15.1",
    architecture: "AMD64",
    nodeSelector: [{ key: "astrolift.io/model-runtime", value: "" }],
    supportedDtypes: [mode === "CPU" ? "FLOAT32" : "AUTO"],
    defaultDtype: mode === "CPU" ? "FLOAT32" : "AUTO",
    defaultMaxModelLen: 256,
    maxModelLenCeiling: 256,
    defaultMaxNumSeqs: 1,
    maxNumSeqsCeiling: 1,
    cpuRequestCeiling: "1",
    memoryRequestCeiling: "4Gi",
    gpuCountCeiling: mode === "CPU" ? 0 : 1,
    hardwareCertified: false,
    hardwareAttested: false,
    hardwareEvidence: null,
  };
}
export function ModelRuntimeSetupPanel(props: ModelRuntimeSetupProps) {
  return <FeedbackContext key={`${props.scopeKey}:${props.mode}:${props.allowed}`} {...props} />;
}
function FeedbackContext(props: ModelRuntimeSetupProps) {
  const t = useTranslations("models.shared.runtimeSetup");
  const [feedback, setFeedback] = useState<string | null>(null),
    epoch = useLease();
  return (
    <div>
      <RuntimeEditor
        key={`${props.scopeKey}:${props.mode}:${props.observation?.clusterVersion ?? "unknown"}:${props.observation?.providerVersion ?? "unknown"}`}
        {...props}
        onSave={async (declaration, review) => {
          const result = await props.onSave(declaration, review);
          if (epoch.current)
            setFeedback(
              result.accepted
                ? result.refreshFailed
                  ? t("savedRefreshFailed")
                  : t("saved")
                : result.message || t("failed")
            );
          return result;
        }}
      />
      {feedback && <p role="status">{feedback}</p>}
    </div>
  );
}
function RuntimeEditor(props: ModelRuntimeSetupProps) {
  const t = useTranslations("models.shared.runtimeSetup"),
    id = useId();
  const [open, setOpen] = useState(false),
    [busy, setBusy] = useState(false),
    [feedback, setFeedback] = useState<string | null>(null);
  const stored = props.observation?.modes.find(
    (row) => row.computeMode === props.mode
  )?.declaration;
  const [draft, setDraft] = useState<RuntimeDeclaration>(() =>
    stored
      ? { ...stored, hardwareEvidence: stored.hardwareEvidence ?? null, hardwareAttested: false }
      : defaults(props.mode)
  );
  const epoch = useLease();
  // The lease is tied to the exact observed target/version by the keyed editor.
  // No late completion can restore feedback or drafts in a different review.
  const update = <K extends keyof RuntimeDeclaration>(key: K, value: RuntimeDeclaration[K]) =>
    setDraft((previous) => ({
      ...previous,
      [key]: value,
      ...(key === "hardwareAttested" ? {} : { hardwareAttested: false }),
    }));
  const fields = [
    "image",
    "version",
    "packageVersion",
    "cpuRequestCeiling",
    "memoryRequestCeiling",
    "defaultMaxModelLen",
    "maxModelLenCeiling",
    "defaultMaxNumSeqs",
    "maxNumSeqsCeiling",
    "gpuCountCeiling",
  ] as const;
  const valid = Boolean(
    draft.image.trim() &&
    draft.nodeSelector.length &&
    draft.nodeSelector.every((row) => row.key.trim() && row.value.trim()) &&
    draft.supportedDtypes.includes(draft.defaultDtype) &&
    (!draft.hardwareCertified || (draft.hardwareAttested && draft.hardwareEvidence?.trim()))
  );
  return (
    <section className="space-y-3 rounded-md border p-4" aria-label={t("title")}>
      <h3 className="font-medium">{t("title")}</h3>
      <p className="text-muted-foreground text-sm">{t("declarationNotice")}</p>
      {props.loading && <p role="status">{t("loading")}</p>}
      {props.error && (
        <div role="alert">
          <p>{props.error}</p>
          <Button type="button" variant="outline" onClick={props.onRetry}>
            {t("retry")}
          </Button>
        </div>
      )}
      {!props.loading && !props.error && !props.observation && <p>{t("unavailable")}</p>}
      {props.observation && (
        <p>
          {props.observation.modes.find((row) => row.computeMode === props.mode)?.configured
            ? t("declared")
            : t("unconfigured")}
        </p>
      )}
      <Button
        type="button"
        variant="outline"
        disabled={!props.allowed || !props.observation || props.loading || Boolean(props.error)}
        onClick={() => setOpen((value) => !value)}
      >
        {open ? t("close") : t("configure")}
      </Button>
      {open && (
        <div className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            {fields.map((field) => (
              <div key={field} className="space-y-1">
                <Label htmlFor={`${id}-${field}`}>{t(field)}</Label>
                <Input
                  id={`${id}-${field}`}
                  disabled={busy || !props.allowed}
                  value={draft[field]}
                  type={typeof draft[field] === "number" ? "number" : "text"}
                  onChange={(event) =>
                    update(
                      field,
                      (typeof draft[field] === "number"
                        ? Number(event.target.value)
                        : event.target.value) as never
                    )
                  }
                />
              </div>
            ))}
          </div>
          <Label htmlFor={`${id}-architecture`}>{t("architecture")}</Label>
          <select
            id={`${id}-architecture`}
            disabled={busy || !props.allowed}
            value={draft.architecture}
            onChange={(event) =>
              update("architecture", event.target.value as RuntimeDeclaration["architecture"])
            }
          >
            <option value="AMD64">amd64</option>
            <option value="ARM64">arm64</option>
          </select>
          <fieldset className="space-y-2">
            <legend>{t("selectors")}</legend>
            {draft.nodeSelector.map((row, index) => (
              <div key={index} className="flex gap-2">
                <Input
                  aria-label={t("selectorKey")}
                  value={row.key}
                  disabled={busy || !props.allowed}
                  onChange={(event) =>
                    update(
                      "nodeSelector",
                      draft.nodeSelector.map((item, i) =>
                        i === index ? { ...item, key: event.target.value } : item
                      )
                    )
                  }
                />
                <Input
                  aria-label={t("selectorValue")}
                  value={row.value}
                  disabled={busy || !props.allowed}
                  onChange={(event) =>
                    update(
                      "nodeSelector",
                      draft.nodeSelector.map((item, i) =>
                        i === index ? { ...item, value: event.target.value } : item
                      )
                    )
                  }
                />
                <Button
                  type="button"
                  variant="outline"
                  disabled={busy || !props.allowed}
                  onClick={() =>
                    update(
                      "nodeSelector",
                      draft.nodeSelector.filter((_, i) => i !== index)
                    )
                  }
                >
                  {t("removeSelector")}
                </Button>
              </div>
            ))}
            <Button
              type="button"
              variant="outline"
              disabled={busy || !props.allowed || draft.nodeSelector.length >= 16}
              onClick={() =>
                update("nodeSelector", [...draft.nodeSelector, { key: "", value: "" }])
              }
            >
              {t("addSelector")}
            </Button>
          </fieldset>
          <fieldset>
            <legend>{t("supportedDtypes")}</legend>
            <div className="flex gap-3">
              {dtypes.map((dtype) => (
                <Label key={dtype}>
                  <input
                    type="checkbox"
                    disabled={busy || !props.allowed}
                    checked={draft.supportedDtypes.includes(dtype)}
                    onChange={(event) =>
                      update(
                        "supportedDtypes",
                        event.target.checked
                          ? [...draft.supportedDtypes, dtype]
                          : draft.supportedDtypes.filter((value) => value !== dtype)
                      )
                    }
                  />{" "}
                  {dtype.toLowerCase()}
                </Label>
              ))}
            </div>
          </fieldset>
          <Label htmlFor={`${id}-defaultDtype`}>{t("defaultDtype")}</Label>
          <select
            id={`${id}-defaultDtype`}
            disabled={busy || !props.allowed}
            value={draft.defaultDtype}
            onChange={(event) => update("defaultDtype", event.target.value as ModelDtype)}
          >
            {dtypes.map((dtype) => (
              <option key={dtype} value={dtype}>
                {dtype.toLowerCase()}
              </option>
            ))}
          </select>
          <Label className="flex gap-2">
            <input
              type="checkbox"
              disabled={busy || !props.allowed}
              checked={draft.hardwareCertified}
              onChange={(event) => update("hardwareCertified", event.target.checked)}
            />
            {t("hardwareCertified")}
          </Label>
          <Label htmlFor={`${id}-evidence`}>{t("hardwareEvidence")}</Label>
          <Input
            id={`${id}-evidence`}
            disabled={busy || !props.allowed}
            maxLength={2048}
            value={draft.hardwareEvidence ?? ""}
            onChange={(event) => update("hardwareEvidence", event.target.value || null)}
          />
          <p className="text-muted-foreground text-sm">{t("evidenceHelp")}</p>
          {draft.hardwareCertified && (
            <Label className="flex gap-2">
              <input
                type="checkbox"
                disabled={busy || !props.allowed}
                checked={draft.hardwareAttested}
                onChange={(event) => update("hardwareAttested", event.target.checked)}
              />
              {t("attestation")}
            </Label>
          )}
          <Button
            type="button"
            disabled={
              busy || !props.allowed || !props.observation || !valid || Boolean(props.error)
            }
            onClick={async () => {
              const observation = props.observation;
              if (!observation || !valid) return;
              setBusy(true);
              setFeedback(null);
              try {
                await props.onSave(draft, observation);
              } catch {
                if (epoch.current) setFeedback(t("unconfirmed"));
              } finally {
                if (epoch.current) setBusy(false);
              }
            }}
          >
            {busy ? t("saving") : t("save")}
          </Button>
        </div>
      )}
      {feedback && <p role="status">{feedback}</p>}
    </section>
  );
}
function useLease() {
  const active = useRef(false);
  useLayoutEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
    };
  }, []);
  return active;
}
