"use client";

import * as React from "react";
import { XIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Sheet,
  SheetContent,
  SheetClose,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";

export type RouteTargetView = {
  key: string;
  app: string;
  environment: string;
  workload: string;
  cluster: string;
};
export type RouteReviewView = {
  hostname: string;
  target: RouteTargetView;
  records: readonly string[];
  mode: "managed" | "manual";
  ingress: string;
  tls: string;
  checkedAt: string;
  reason: string | null;
};
export type DomainAppConnectionSheetProps = {
  open: boolean;
  initialStep?: 0 | 1 | 2;
  onOpenChange: (open: boolean) => void;
  zone: string;
  hostnameKind: "apex" | "subdomain";
  subdomain: string;
  onHostname: (kind: "apex" | "subdomain", subdomain: string) => void;
  hostnameValid: boolean;
  targets: readonly RouteTargetView[];
  targetKey: string | null;
  onTarget: (key: string) => void;
  readState: "loading" | "refused" | "error" | "ready";
  readReason: string | null;
  onRefresh?: () => void;
  hasPreviousPage: boolean;
  hasNextPage: boolean;
  onPreviousPage?: () => void;
  onNextPage?: () => void;
  review: RouteReviewView | null;
  reviewing: boolean;
  reviewError: string | null;
  canReview: boolean;
  onReview?: () => void;
  canPrepare: boolean;
  onPrepare?: () => void;
  canAttach: boolean;
  onAttach?: () => void;
  busy: boolean;
  outcome: "none" | "pending" | "unknown" | "recorded";
  onRecover?: () => void;
};

export function DomainAppConnectionSheet(props: DomainAppConnectionSheetProps) {
  return props.open ? <OpenConnectionSheet {...props} /> : null;
}

function OpenConnectionSheet(props: DomainAppConnectionSheetProps) {
  const t = useTranslations("domainAppRouting");
  const d = useTranslations("managedDomains");
  const [step, setStep] = React.useState(props.initialStep ?? 0);
  const nameId = React.useId();
  const targetId = React.useId();
  const locked = props.busy || props.outcome !== "none";
  const freshTargets = props.readState === "ready";
  const selected = props.targets.find((target) => target.key === props.targetKey);
  const preview = props.hostnameKind === "apex" ? props.zone : `${props.subdomain}.${props.zone}`;
  const currentReview = Boolean(
    props.hostnameValid &&
    !props.reviewing &&
    !props.reviewError &&
    freshTargets &&
    props.review &&
    props.review.hostname === preview &&
    props.review.target.key === props.targetKey
  );
  return (
    <Sheet open={props.open} onOpenChange={props.onOpenChange}>
      <SheetContent
        showCloseButton={false}
        className="w-full max-w-full overflow-y-auto sm:max-w-xl"
      >
        <SheetClose asChild>
          <Button variant="ghost" size="icon-sm" className="absolute top-3 right-3">
            <XIcon />
            <span className="sr-only">{d("cancel")}</span>
          </Button>
        </SheetClose>
        <SheetHeader className="pr-12">
          <SheetTitle>{t("connect")}</SheetTitle>
          <SheetDescription>{t("intro")}</SheetDescription>
        </SheetHeader>
        <div className="space-y-5 px-4 pb-6">
          <ol aria-label={t("steps")} className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
            {(["hostnameStep", "targetStep", "reviewStep"] as const).map((key, index) => (
              <li key={key} aria-current={step === index ? "step" : undefined}>
                {index + 1}. {t(key)}
              </li>
            ))}
          </ol>
          {props.outcome !== "none" && (
            <div role="status" className="space-y-3 rounded-lg border p-3">
              <p>{t(props.outcome === "unknown" ? "unknownHelp" : "recordedHelp")}</p>
              <p className="text-muted-foreground text-sm">{t("observationHelp")}</p>
              <Button disabled={props.busy || !props.onRecover} onClick={props.onRecover}>
                {t("recover")}
              </Button>
            </div>
          )}
          {step === 0 && (
            <section className="space-y-4">
              <fieldset disabled={locked} className="space-y-2">
                <legend className="mb-2 font-medium">{d("hostname")}</legend>
                {(["apex", "subdomain"] as const).map((kind) => (
                  <label key={kind} className="flex items-center gap-2">
                    <input
                      type="radio"
                      name={nameId}
                      value={kind}
                      checked={props.hostnameKind === kind}
                      onChange={() => props.onHostname(kind, props.subdomain)}
                    />
                    {t(kind)}
                  </label>
                ))}
                {props.hostnameKind === "subdomain" && (
                  <div className="space-y-2">
                    <Label htmlFor={nameId}>{t("subdomain")}</Label>
                    <Input
                      id={nameId}
                      value={props.subdomain}
                      onChange={(event) => props.onHostname("subdomain", event.target.value)}
                      autoComplete="off"
                      aria-describedby={`${nameId}-help`}
                    />
                    <p id={`${nameId}-help`} className="text-muted-foreground text-sm">
                      {t("subdomainHelp")}
                    </p>
                  </div>
                )}
              </fieldset>
              <p className="font-mono text-sm break-all">{preview}</p>
              <Button disabled={locked || !props.hostnameValid} onClick={() => setStep(1)}>
                {t("next")}
              </Button>
            </section>
          )}
          {step === 1 && (
            <section className="space-y-4">
              <p className="text-muted-foreground text-sm">{t("targetHelp")}</p>
              {props.readState !== "ready" && (
                <div role={props.readState === "loading" ? "status" : "alert"}>
                  <p>{d(props.readState === "loading" ? "loading" : "readFailed")}</p>
                  {props.readReason && (
                    <code className="text-xs break-all">{props.readReason}</code>
                  )}
                </div>
              )}
              {freshTargets && !props.targets.length && <p>{t("empty")}</p>}
              {freshTargets && props.targets.length > 0 && (
                <div className="space-y-2">
                  <Label htmlFor={targetId}>{t("targetStep")}</Label>
                  <select
                    id={targetId}
                    className="bg-background w-full min-w-0 rounded-md border p-2"
                    value={selected?.key ?? ""}
                    disabled={locked}
                    onChange={(event) => props.onTarget(event.target.value)}
                  >
                    <option value="">{t("selectTarget")}</option>
                    {props.targets.map((target) => (
                      <option key={target.key} value={target.key}>
                        {target.app} · {target.environment} · {target.workload}
                      </option>
                    ))}
                  </select>
                  {selected && <p className="text-sm break-all">{selected.cluster}</p>}
                </div>
              )}
              <div className="flex flex-wrap gap-2">
                <Button
                  variant="outline"
                  disabled={locked || !props.onRefresh}
                  onClick={props.onRefresh}
                >
                  {d("refresh")}
                </Button>
                {props.hasPreviousPage && (
                  <Button
                    variant="outline"
                    disabled={locked || !props.onPreviousPage}
                    onClick={props.onPreviousPage}
                  >
                    {t("previousPage")}
                  </Button>
                )}
                {props.hasNextPage && (
                  <Button
                    variant="outline"
                    disabled={locked || !props.onNextPage}
                    onClick={props.onNextPage}
                  >
                    {t("nextPage")}
                  </Button>
                )}
              </div>
              <div className="flex flex-wrap gap-2">
                <Button variant="outline" disabled={locked} onClick={() => setStep(0)}>
                  {d("back")}
                </Button>
                <Button
                  disabled={
                    locked ||
                    props.reviewing ||
                    !freshTargets ||
                    !selected ||
                    !props.canReview ||
                    !props.onReview
                  }
                  onClick={() => {
                    props.onReview?.();
                    setStep(2);
                  }}
                >
                  {t("review")}
                </Button>
              </div>
            </section>
          )}
          {step === 2 && (
            <section className="space-y-4">
              <p className="text-muted-foreground text-sm">{t("reviewHelp")}</p>
              {props.reviewing && <p role="status">{d("running")}</p>}
              {props.reviewError && (
                <p role="alert" className="break-words">
                  {props.reviewError}
                </p>
              )}
              {props.review && !props.reviewing && (
                <>
                  <dl className="space-y-3 text-sm">
                    {[
                      [d("hostname"), props.review.hostname],
                      [d("application"), props.review.target.app],
                      [d("environment"), props.review.target.environment],
                      [t("workload"), props.review.target.workload],
                      [d("cluster"), props.review.target.cluster],
                      [d("ingress"), props.review.ingress],
                      [d("tls"), props.review.tls],
                      [d("checkedAt"), props.review.checkedAt],
                    ].map(([label, value]) => (
                      <div key={label}>
                        <dt className="text-muted-foreground">{label}</dt>
                        <dd className="break-all">{value}</dd>
                      </div>
                    ))}
                  </dl>
                  <div className="space-y-2 rounded-lg border p-3">
                    <h3 className="font-medium">{t("dnsProposal")}</h3>
                    {props.review.records.map((record, index) => (
                      <p key={index} className="font-mono text-xs break-all">
                        {record}
                      </p>
                    ))}
                  </div>
                  {props.review.reason && (
                    <code className="block text-xs break-all">{props.review.reason}</code>
                  )}
                  {props.review.mode === "manual" && <p>{t("manualHelp")}</p>}
                </>
              )}
              <div className="flex flex-wrap gap-2">
                <Button variant="outline" disabled={locked} onClick={() => setStep(1)}>
                  {d("back")}
                </Button>
                {props.onPrepare && (
                  <Button
                    variant="outline"
                    disabled={locked || !currentReview || !props.canPrepare}
                    onClick={props.onPrepare}
                  >
                    {t("prepare")}
                  </Button>
                )}
                <Button
                  disabled={
                    locked ||
                    props.reviewing ||
                    !currentReview ||
                    props.review?.mode !== "managed" ||
                    !props.canAttach ||
                    !props.onAttach
                  }
                  onClick={props.onAttach}
                >
                  {t("attach")}
                </Button>
              </div>
            </section>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
