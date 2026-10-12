"use client";

import { ArrowLeftRightIcon, InfoIcon, ShieldQuestionIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import type * as React from "react";

import type { Principal } from "@/components/access/access-model";
import { AccessCompare, AccessExplainer } from "@/components/access/AccessExplainer";
import { PermissionPicker } from "@/components/access/PermissionPicker";
import { PrincipalPicker } from "@/components/access/PrincipalPicker";
import { ScopePicker } from "@/components/access/ScopePicker";
import { ShellHeader } from "@/components/shell/ShellHeader";
import { Button } from "@/components/ui/button";
import { Section } from "@/components/ui/section";

import { permissionsCrumbs } from "../access/admin-crumbs";
import type { usePermissionsDiagnostics } from "./use-permissions-diagnostics";

interface Loadable {
  loading: boolean;
  error: { message: string } | null;
  onRetry: () => void;
}

type Hook = ReturnType<typeof usePermissionsDiagnostics>;

/** The hook's shape, with its retries typed as plain callbacks so fixtures can pass them. */
export type PermissionsDiagnosticsViewProps = Omit<Hook, "scopeTree" | "explainer" | "compare"> & {
  scopeTree: Loadable & { roots: Hook["scopeTree"]["roots"] };
  explainer: Loadable & { diagnosis: Hook["explainer"]["diagnosis"] };
  compare: Loadable & { comparison: Hook["compare"]["comparison"] };
};

/**
 * Admin › Permissions › Check access (design 3.7), on the Diagnostics route
 * it replaces: "Can <who> <do what> on <which>?" with a person picker (the
 * viewer by default), the catalog grouped by area and the scope tree, then
 * the answer as the resolver's reasoning chain. Compare puts two people side
 * by side instead. Detail-like (spec 44 §5.2): the shared header, then the
 * question and its answer; no tab row, no list. Pure.
 */
export function PermissionsDiagnosticsView(props: PermissionsDiagnosticsViewProps) {
  const { comparing, onCompareToggle } = props;
  const t = useTranslations("adminCheckAccess");
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-6 p-6">
      <ShellHeader
        crumbs={permissionsCrumbs("diagnostics")}
        title={comparing ? t("compareTitle") : t("checkTitle")}
        primaryAction={
          <Button variant="outline" size="sm" onClick={() => onCompareToggle(!comparing)}>
            {comparing ? (
              <>
                <ShieldQuestionIcon className="size-4" /> {t("checkOne")}
              </>
            ) : (
              <>
                <ArrowLeftRightIcon className="size-4" /> {t("compareTwo")}
              </>
            )}
          </Button>
        }
      />
      {comparing ? <CompareBody {...props} /> : <CheckBody {...props} />}
    </div>
  );
}

function meQuickPick(me: Principal | null, label: string) {
  return me ? [{ label, principal: me }] : [];
}

function CheckBody({
  me,
  who,
  whoSearch,
  onWhoChange,
  permission,
  onPermissionChange,
  scope,
  onScopeChange,
  scopeTree,
  explainer,
  bindingHref,
}: PermissionsDiagnosticsViewProps) {
  const t = useTranslations("adminCheckAccess");
  return (
    <>
      <p className="max-w-3xl min-w-0 text-base [overflow-wrap:anywhere]" aria-live="polite">
        {t.rich("question", {
          who: () => <Slot filled={Boolean(who)}>{who?.name ?? t("someone")}</Slot>,
          permission: () => (
            <Slot filled={Boolean(permission)} mono>
              {permission ?? t("doSomething")}
            </Slot>
          ),
          scope: () => (
            <Slot filled={Boolean(scope)} mono={Boolean(scope)}>
              {scope?.name ?? t("theOrganization")}
            </Slot>
          ),
        })}
      </p>

      <div className="grid min-w-0 gap-5 lg:grid-cols-3">
        <PrincipalPicker
          label={t("who")}
          value={who}
          onChange={onWhoChange}
          search={whoSearch}
          quickPicks={meQuickPick(me, t("me"))}
        />
        <PermissionPicker value={permission} onChange={onPermissionChange} />
        <div className="flex min-w-0 flex-col gap-2">
          <div className="flex min-w-0 items-center justify-between gap-2">
            <span className="text-muted-foreground text-xs font-medium tracking-wide uppercase">
              {t("on")}
            </span>
            {scope && (
              <Button size="sm" variant="ghost" onClick={() => onScopeChange(null)}>
                {t("wholeOrg")}
              </Button>
            )}
          </div>
          <ScopePicker
            label={t("on")}
            roots={scopeTree.roots}
            loading={scopeTree.loading}
            error={scopeTree.error}
            onRetry={scopeTree.onRetry}
            value={scope}
            onChange={(n) => onScopeChange({ kind: n.kind, id: n.id, name: n.name })}
          />
        </div>
      </div>

      <Section title={t("answer")} divided className="min-w-0">
        <AccessExplainer
          diagnosis={explainer.diagnosis}
          target={scope}
          loading={explainer.loading}
          error={explainer.error}
          onRetry={explainer.onRetry}
          bindingHref={bindingHref}
        />
        <Scoping />
      </Section>
    </>
  );
}

function CompareBody({
  me,
  who,
  whoSearch,
  onWhoChange,
  other,
  otherSearch,
  onOtherChange,
  compare,
}: PermissionsDiagnosticsViewProps) {
  const t = useTranslations("adminCheckAccess");
  return (
    <>
      <p className="max-w-3xl min-w-0 text-base [overflow-wrap:anywhere]" aria-live="polite">
        {t.rich("compareQuestion", {
          who: () => <Slot filled={Boolean(who)}>{who?.name ?? t("someone")}</Slot>,
          other: () => <Slot filled={Boolean(other)}>{other?.name ?? t("someoneElse")}</Slot>,
        })}
      </p>
      <div className="grid min-w-0 gap-5 lg:grid-cols-2">
        <PrincipalPicker
          label={t("firstPerson")}
          value={who}
          onChange={onWhoChange}
          search={whoSearch}
          quickPicks={meQuickPick(me, t("me"))}
        />
        <PrincipalPicker
          label={t("secondPerson")}
          value={other}
          onChange={onOtherChange}
          search={otherSearch}
        />
      </div>
      <Section title={t("sideBySide")} divided className="min-w-0">
        <AccessCompare
          comparison={compare.comparison}
          loading={compare.loading}
          error={compare.error}
          onRetry={compare.onRetry}
        />
        <Scoping compare />
      </Section>
    </>
  );
}

/** Who may ask and what the answer covers, said once under the answer. */
function Scoping({ compare = false }: { compare?: boolean }) {
  const t = useTranslations("adminCheckAccess");
  return (
    <p className="text-muted-foreground flex max-w-3xl min-w-0 items-start gap-2 text-xs">
      <InfoIcon aria-hidden className="mt-0.5 size-3.5 shrink-0" />
      <span className="min-w-0">
        {compare
          ? t("compareNeeds", { permission: "org.manage_members" })
          : t("checkNeeds", { permission: "org.manage_members" })}{" "}
        {t("answerCovers")}
      </span>
    </p>
  );
}

function Slot({
  filled,
  mono = false,
  children,
}: {
  filled: boolean;
  mono?: boolean;
  children: React.ReactNode;
}) {
  return (
    <span
      className={
        filled
          ? `text-foreground font-medium ${mono ? "font-mono text-sm" : ""}`
          : "text-muted-foreground italic"
      }
    >
      {children}
    </span>
  );
}
