"use client";

import { ArrowLeftRightIcon, InfoIcon, ShieldQuestionIcon } from "lucide-react";
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
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-6 p-6">
      <ShellHeader
        crumbs={permissionsCrumbs("diagnostics")}
        title={comparing ? "Compare access" : "Check access"}
        primaryAction={
          <Button variant="outline" size="sm" onClick={() => onCompareToggle(!comparing)}>
            {comparing ? (
              <>
                <ShieldQuestionIcon className="size-4" /> Check one permission
              </>
            ) : (
              <>
                <ArrowLeftRightIcon className="size-4" /> Compare two people
              </>
            )}
          </Button>
        }
      />
      {comparing ? <CompareBody {...props} /> : <CheckBody {...props} />}
    </div>
  );
}

function meQuickPick(me: Principal | null) {
  return me ? [{ label: "Me", principal: me }] : [];
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
  return (
    <>
      <p className="max-w-3xl min-w-0 text-base [overflow-wrap:anywhere]" aria-live="polite">
        Can <Slot filled={Boolean(who)}>{who?.name ?? "someone"}</Slot>{" "}
        <Slot filled={Boolean(permission)} mono>
          {permission ?? "do something"}
        </Slot>{" "}
        on{" "}
        <Slot filled={Boolean(scope)} mono={Boolean(scope)}>
          {scope?.name ?? "the organization"}
        </Slot>
        ?
      </p>

      <div className="grid min-w-0 gap-5 lg:grid-cols-3">
        <PrincipalPicker
          label="Who"
          value={who}
          onChange={onWhoChange}
          search={whoSearch}
          quickPicks={meQuickPick(me)}
        />
        <PermissionPicker value={permission} onChange={onPermissionChange} />
        <div className="flex min-w-0 flex-col gap-2">
          <div className="flex min-w-0 items-center justify-between gap-2">
            <span className="text-muted-foreground text-xs font-medium tracking-wide uppercase">
              On
            </span>
            {scope && (
              <Button size="sm" variant="ghost" onClick={() => onScopeChange(null)}>
                Whole organization
              </Button>
            )}
          </div>
          <ScopePicker
            label="On"
            roots={scopeTree.roots}
            loading={scopeTree.loading}
            error={scopeTree.error}
            onRetry={scopeTree.onRetry}
            value={scope}
            onChange={(n) => onScopeChange({ kind: n.kind, id: n.id, name: n.name })}
          />
        </div>
      </div>

      <Section title="Answer" divided className="min-w-0">
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
  return (
    <>
      <p className="max-w-3xl min-w-0 text-base [overflow-wrap:anywhere]" aria-live="polite">
        What can <Slot filled={Boolean(who)}>{who?.name ?? "someone"}</Slot> do that{" "}
        <Slot filled={Boolean(other)}>{other?.name ?? "someone else"}</Slot> cannot, and the other
        way round?
      </p>
      <div className="grid min-w-0 gap-5 lg:grid-cols-2">
        <PrincipalPicker
          label="First person"
          value={who}
          onChange={onWhoChange}
          search={whoSearch}
          quickPicks={meQuickPick(me)}
        />
        <PrincipalPicker
          label="Second person"
          value={other}
          onChange={onOtherChange}
          search={otherSearch}
        />
      </div>
      <Section title="Side by side" divided className="min-w-0">
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

/** What the backend answers today (design 6, item 8), said once under the answer. */
function Scoping({ compare = false }: { compare?: boolean }) {
  return (
    <p className="text-muted-foreground flex max-w-3xl min-w-0 items-start gap-2 text-xs">
      <InfoIcon aria-hidden className="mt-0.5 size-3.5 shrink-0" />
      <span className="min-w-0">
        {compare
          ? "Comparing is open to superusers only for now."
          : "You can check your own access; checking someone else needs a superuser for now."}{" "}
        Grants through team shares and IdP groups are not in the answer yet.
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
