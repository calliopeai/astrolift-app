"use client";

import * as React from "react";
import { useTranslations } from "next-intl";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { domainDefaultReasonKey } from "./domain-default-reasons";

export type DomainDefaultChoice = "none" | "tenant_apps" | "preview_envs" | "both";
export type DomainDefaultSettingsProps = {
  zone: string;
  version: number;
  defaultsFingerprint: string | null;
  declared: DomainDefaultChoice;
  effectiveTenant: string | null;
  effectivePreview: string | null;
  canUpdate: boolean;
  canSetDefaults: boolean;
  loading: boolean;
  busy: boolean;
  reason: string | null;
  uncertain: boolean;
  actionMessage?: string | null;
  actionError?: string | null;
  onSave?: (
    choice: DomainDefaultChoice,
    expectedVersion: number,
    defaultsFingerprint: string
  ) => Promise<void>;
  onRefresh?: () => void;
};
const choiceKeys = {
  none: "none",
  tenant_apps: "tenantApps",
  preview_envs: "previewEnvironments",
  both: "both",
} as const;

export function DomainDefaultSettings(props: DomainDefaultSettingsProps) {
  return (
    <DefaultSettingsView
      key={`${props.zone}:${props.version}:${props.defaultsFingerprint}:${props.declared}`}
      {...props}
    />
  );
}

function DefaultSettingsView(props: DomainDefaultSettingsProps) {
  const t = useTranslations("domainDefaults");
  const d = useTranslations("managedDomains");
  const id = React.useId();
  const [choice, setChoice] = React.useState(props.declared);
  const [review, setReview] = React.useState<{
    choice: DomainDefaultChoice;
    version: number;
    fingerprint: string;
  } | null>(null);
  const current =
    !props.loading &&
    !props.busy &&
    !props.uncertain &&
    props.canUpdate &&
    Number.isSafeInteger(props.version) &&
    props.version > 0 &&
    /^[a-f0-9]{64}$/.test(props.defaultsFingerprint ?? "") &&
    Boolean(props.onSave);
  const choiceAllowed = current && (choice === "none" || props.canSetDefaults);
  return (
    <section className="min-w-0 space-y-4 rounded-lg border p-4">
      <h2 className="font-semibold">{t("title")}</h2>
      <p className="text-muted-foreground text-sm">{t("help")}</p>
      <dl className="grid gap-3 text-sm sm:grid-cols-2">
        <div>
          <dt className="text-muted-foreground">{t("declared")}</dt>
          <dd>{d(choiceKeys[props.declared])}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">{d("zone")}</dt>
          <dd className="break-all">{props.zone}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">{d("effectiveTenant")}</dt>
          <dd className="break-all">{props.effectiveTenant ?? d("unknown")}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">{d("effectivePreview")}</dt>
          <dd className="break-all">{props.effectivePreview ?? d("unknown")}</dd>
        </div>
      </dl>
      {props.loading && <p role="status">{d("loading")}</p>}
      {props.reason && (
        <p role="alert" className="text-sm break-words">
          <span>{t(domainDefaultReasonKey(props.reason))}</span>
          <code className="mt-1 block text-xs break-all">{props.reason}</code>
        </p>
      )}
      {props.uncertain && <p role="status">{t("uncertain")}</p>}
      {props.actionMessage && <p role="status">{props.actionMessage}</p>}
      {props.actionError && <p role="alert">{props.actionError}</p>}
      <div className="space-y-2">
        <Label htmlFor={id}>{t("useFor")}</Label>
        <select
          id={id}
          className="bg-background w-full rounded-md border p-2"
          value={choice}
          disabled={!current}
          onChange={(event) => {
            setChoice(event.target.value as DomainDefaultChoice);
            setReview(null);
          }}
        >
          {(Object.keys(choiceKeys) as DomainDefaultChoice[]).map((value) => (
            <option key={value} value={value}>
              {d(choiceKeys[value])}
            </option>
          ))}
        </select>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button
          disabled={!choiceAllowed}
          onClick={() => {
            if (props.defaultsFingerprint)
              setReview({ choice, version: props.version, fingerprint: props.defaultsFingerprint });
          }}
        >
          {t("review")}
        </Button>
        <Button
          variant="outline"
          disabled={props.loading || props.busy || !props.onRefresh}
          onClick={props.onRefresh}
        >
          {d("refresh")}
        </Button>
      </div>
      <ConfirmDialog
        open={review !== null}
        onOpenChange={(open) => {
          if (!open) setReview(null);
        }}
        title={t("review")}
        description={`${t("reviewHelp")} ${props.zone}: ${d(choiceKeys[review?.choice ?? choice])}`}
        confirmLabel={t("save")}
        confirmDisabled={
          !choiceAllowed ||
          !review ||
          review.version !== props.version ||
          review.choice !== choice ||
          review.fingerprint !== props.defaultsFingerprint
        }
        onConfirm={async () => {
          if (
            !choiceAllowed ||
            !review ||
            review.version !== props.version ||
            review.choice !== choice ||
            review.fingerprint !== props.defaultsFingerprint ||
            !props.onSave
          )
            return;
          await props.onSave(review.choice, review.version, review.fingerprint);
          setReview(null);
        }}
      />
    </section>
  );
}
