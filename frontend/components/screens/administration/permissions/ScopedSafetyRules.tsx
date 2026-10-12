"use client";

import { useTranslations } from "next-intl";
import * as React from "react";

import type { ScopeNode } from "@/components/access/access-model";
import { ScopePicker } from "@/components/access/ScopePicker";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export type ScopedKind = "TEAM" | "APP" | "USER";

export type ScopedRule = {
  action: string;
  scopeKind: string;
  scopeId?: string | null;
  scopeLabel: string;
  enabled?: boolean | null;
  allowSelfApproval?: boolean | null;
  requireApproval?: boolean | null;
  minimumApprovals?: number | null;
};

export type ScopedRulePatch = {
  enabled?: boolean;
  allowSelfApproval?: boolean;
  requireApproval?: boolean;
  minimumApprovals?: number;
};

export type ScopedSafetyRulesProps = {
  actions: { action: string; label: string; approvable: boolean }[];
  rules: ScopedRule[];
  scopeTree: {
    roots: ScopeNode[];
    loading: boolean;
    error: { message: string } | null;
    onRetry: () => void;
  };
  people: {
    query: string;
    onQuery: (query: string) => void;
    results: { id: string; name: string; secondary: string }[];
    loading: boolean;
  };
  saving: boolean;
  error: string | null;
  onAdd: (
    target: { kind: ScopedKind; id: string },
    action: string,
    patch: ScopedRulePatch
  ) => Promise<boolean>;
  onClear: (rule: ScopedRule) => void;
};

const KINDS: ScopedKind[] = ["TEAM", "APP", "USER"];

/** The `actionSafety.scoped` translator, or one bound to it. */
type ScopedTranslator = (key: string, values?: Record<string, string | number>) => string;

/** What one narrower rule adds, in words. */
export function describeRule(rule: ScopedRule, t: ScopedTranslator): string {
  const parts: string[] = [];
  if (rule.enabled) parts.push(t("adds.asksBefore"));
  if (rule.requireApproval) {
    parts.push(t("adds.needsApprovers", { count: rule.minimumApprovals ?? 1 }));
  }
  if (rule.allowSelfApproval === false) parts.push(t("adds.noSelfApproval"));
  return parts.join(" · ") || t("adds.nothing");
}

/**
 * Rules for one team, app or person (#2203). They can only add to what the
 * organization asks (strictest wins), so the form offers additions: ask
 * before, require approval with a quorum, and forbid approving your own.
 * Removing one makes that scope inherit again.
 */
export function ScopedSafetyRules({
  actions,
  rules,
  scopeTree,
  people,
  saving,
  error,
  onAdd,
  onClear,
}: ScopedSafetyRulesProps) {
  const t = useTranslations("actionSafety.scoped");
  const kindLabel = (k: string) =>
    (KINDS as string[]).includes(k) ? t(`kinds.${k as ScopedKind}`) : k;
  const [kind, setKind] = React.useState<ScopedKind>("TEAM");
  const [target, setTarget] = React.useState<{ id: string; name: string } | null>(null);
  const [action, setAction] = React.useState(actions[0]?.action ?? "");
  const [askBefore, setAskBefore] = React.useState(false);
  const [approval, setApproval] = React.useState(false);
  const [quorum, setQuorum] = React.useState(1);
  const [noSelf, setNoSelf] = React.useState(false);
  const approvable = actions.find((a) => a.action === action)?.approvable ?? false;
  const labelOf = (name: string) => actions.find((a) => a.action === name)?.label ?? name;
  const adds = askBefore || (approvable && (approval || noSelf));

  async function add() {
    if (!target || !adds) return;
    const patch: ScopedRulePatch = {};
    if (askBefore) patch.enabled = true;
    if (approvable && approval)
      Object.assign(patch, { requireApproval: true, minimumApprovals: quorum });
    if (approvable && noSelf) patch.allowSelfApproval = false;
    if (await onAdd({ kind, id: target.id }, action, patch)) {
      setTarget(null);
      setAskBefore(false);
      setApproval(false);
      setQuorum(1);
      setNoSelf(false);
    }
  }

  return (
    <section aria-labelledby="scoped-rules" className="space-y-3">
      <div>
        <h2 id="scoped-rules" className="text-sm font-medium">
          {t("title")}
        </h2>
        <p className="text-muted-foreground text-xs">{t("description")}</p>
      </div>
      {error && (
        <p role="alert" className="text-destructive text-sm">
          {error}
        </p>
      )}
      {rules.length === 0 ? (
        <p className="text-muted-foreground text-sm">{t("empty")}</p>
      ) : (
        <ul className="divide-y rounded-sm border text-sm">
          {rules.map((rule) => (
            <li
              key={`${rule.scopeKind}:${rule.scopeId}:${rule.action}`}
              aria-label={`${labelOf(rule.action)}: ${rule.scopeLabel}`}
              className="flex flex-wrap items-center gap-x-4 gap-y-1 p-2"
            >
              <span className="min-w-0 flex-1">
                {labelOf(rule.action)}{" "}
                <span className="text-muted-foreground text-xs">
                  {t("forKind", { kind: kindLabel(rule.scopeKind) })}
                </span>{" "}
                <span className="font-mono text-xs">{rule.scopeLabel}</span>
                <span className="text-muted-foreground block text-xs">{describeRule(rule, t)}</span>
              </span>
              <Button size="sm" variant="ghost" disabled={saving} onClick={() => onClear(rule)}>
                {t("remove")}
              </Button>
            </li>
          ))}
        </ul>
      )}

      <fieldset className="space-y-3 rounded-sm border p-3 text-sm" disabled={saving}>
        <legend className="px-1 text-xs font-medium">{t("addLegend")}</legend>
        <div className="flex flex-wrap items-center gap-3">
          <Select
            value={kind}
            onValueChange={(value) => {
              setKind(value as ScopedKind);
              setTarget(null);
            }}
          >
            <SelectTrigger size="sm" className="w-auto" aria-label={t("appliesTo")}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {KINDS.map((k) => (
                <SelectItem key={k} value={k}>
                  {kindLabel(k)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={action} onValueChange={setAction}>
            <SelectTrigger size="sm" className="w-auto" aria-label={t("action")}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {actions.map((a) => (
                <SelectItem key={a.action} value={a.action}>
                  {a.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {target && (
            <span className="text-xs">
              {kindLabel(kind)}: <span className="font-mono">{target.name}</span>
            </span>
          )}
        </div>

        {kind === "USER" ? (
          <div className="space-y-1">
            <Input
              value={people.query}
              onChange={(event) => people.onQuery(event.target.value)}
              placeholder={t("searchPeoplePlaceholder")}
              aria-label={t("searchPeople")}
              className="max-w-sm"
            />
            {people.loading ? (
              <p role="status" className="text-muted-foreground text-xs">
                {t("searching")}
              </p>
            ) : (
              people.results.length > 0 && (
                <ul aria-label={t("people")} className="max-w-sm divide-y rounded-sm border">
                  {people.results.map((person) => (
                    <li key={person.id}>
                      <button
                        type="button"
                        className="hover:bg-muted w-full p-2 text-left text-xs"
                        aria-pressed={target?.id === person.id}
                        onClick={() => setTarget({ id: person.id, name: person.name })}
                      >
                        {person.name}{" "}
                        <span className="text-muted-foreground">{person.secondary}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )
            )}
          </div>
        ) : (
          <ScopePicker
            roots={scopeTree.roots}
            loading={scopeTree.loading}
            error={scopeTree.error}
            onRetry={scopeTree.onRetry}
            value={target ? { kind, id: target.id, name: target.name } : null}
            onChange={(node) => setTarget({ id: node.id, name: node.name })}
            selectable={(node) =>
              node.kind === kind ? true : kind === "TEAM" ? t("pickTeam") : t("pickApp")
            }
            label={kind === "TEAM" ? t("teams") : t("apps")}
            className="max-h-56 max-w-sm overflow-auto"
          />
        )}

        <div className="flex flex-wrap items-center gap-4">
          <label className="flex items-center gap-2">
            <Checkbox checked={askBefore} onCheckedChange={(v) => setAskBefore(v === true)} />
            <span className="text-xs">{t("askBefore")}</span>
          </label>
          {approvable && (
            <>
              <label className="flex items-center gap-2">
                <Checkbox checked={approval} onCheckedChange={(v) => setApproval(v === true)} />
                <span className="text-xs">{t("requireApproval")}</span>
              </label>
              {approval && (
                <Select value={String(quorum)} onValueChange={(v) => setQuorum(Number(v))}>
                  <SelectTrigger size="sm" className="w-auto" aria-label={t("approversNeeded")}>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {[1, 2, 3, 4, 5].map((n) => (
                      <SelectItem key={n} value={String(n)}>
                        {t("approvers", { count: n })}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
              <label className="flex items-center gap-2">
                <Checkbox checked={noSelf} onCheckedChange={(v) => setNoSelf(v === true)} />
                <span className="text-xs">{t("noSelf")}</span>
              </label>
            </>
          )}
          <Button size="sm" disabled={!target || !adds} onClick={() => void add()}>
            {t("add")}
          </Button>
        </div>
      </fieldset>
    </section>
  );
}
