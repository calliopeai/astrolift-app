"use client";

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

const KIND_LABEL: Record<ScopedKind, string> = { TEAM: "Team", APP: "App", USER: "Person" };

/** What one narrower rule adds, in words. */
export function describeRule(rule: ScopedRule): string {
  const parts: string[] = [];
  if (rule.enabled) parts.push("asks before");
  if (rule.requireApproval) {
    const n = rule.minimumApprovals ?? 1;
    parts.push(`needs ${n} ${n === 1 ? "approver" : "approvers"}`);
  }
  if (rule.allowSelfApproval === false) parts.push("no self-approval");
  return parts.join(" · ") || "adds nothing";
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
          Rules for teams, apps and people
        </h2>
        <p className="text-muted-foreground text-xs">
          These add to the organization&apos;s gates, never remove them.
        </p>
      </div>
      {error && (
        <p role="alert" className="text-destructive text-sm">
          {error}
        </p>
      )}
      {rules.length === 0 ? (
        <p className="text-muted-foreground text-sm">No team, app or person rules.</p>
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
                  for {KIND_LABEL[rule.scopeKind as ScopedKind] ?? rule.scopeKind}
                </span>{" "}
                <span className="font-mono text-xs">{rule.scopeLabel}</span>
                <span className="text-muted-foreground block text-xs">{describeRule(rule)}</span>
              </span>
              <Button size="sm" variant="ghost" disabled={saving} onClick={() => onClear(rule)}>
                Remove
              </Button>
            </li>
          ))}
        </ul>
      )}

      <fieldset className="space-y-3 rounded-sm border p-3 text-sm" disabled={saving}>
        <legend className="px-1 text-xs font-medium">Add a rule</legend>
        <div className="flex flex-wrap items-center gap-3">
          <Select
            value={kind}
            onValueChange={(value) => {
              setKind(value as ScopedKind);
              setTarget(null);
            }}
          >
            <SelectTrigger size="sm" className="w-auto" aria-label="Applies to">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {(Object.keys(KIND_LABEL) as ScopedKind[]).map((k) => (
                <SelectItem key={k} value={k}>
                  {KIND_LABEL[k]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={action} onValueChange={setAction}>
            <SelectTrigger size="sm" className="w-auto" aria-label="Action">
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
              {KIND_LABEL[kind]}: <span className="font-mono">{target.name}</span>
            </span>
          )}
        </div>

        {kind === "USER" ? (
          <div className="space-y-1">
            <Input
              value={people.query}
              onChange={(event) => people.onQuery(event.target.value)}
              placeholder="Search people by name or email"
              aria-label="Search people"
              className="max-w-sm"
            />
            {people.loading ? (
              <p role="status" className="text-muted-foreground text-xs">
                Searching…
              </p>
            ) : (
              people.results.length > 0 && (
                <ul aria-label="People" className="max-w-sm divide-y rounded-sm border">
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
              node.kind === kind ? true : `Pick ${kind === "TEAM" ? "a team" : "an app"}`
            }
            label={kind === "TEAM" ? "Teams" : "Apps"}
            className="max-h-56 max-w-sm overflow-auto"
          />
        )}

        <div className="flex flex-wrap items-center gap-4">
          <label className="flex items-center gap-2">
            <Checkbox checked={askBefore} onCheckedChange={(v) => setAskBefore(v === true)} />
            <span className="text-xs">Ask before (confirm and sign in again)</span>
          </label>
          {approvable && (
            <>
              <label className="flex items-center gap-2">
                <Checkbox checked={approval} onCheckedChange={(v) => setApproval(v === true)} />
                <span className="text-xs">Require approval</span>
              </label>
              {approval && (
                <Select value={String(quorum)} onValueChange={(v) => setQuorum(Number(v))}>
                  <SelectTrigger size="sm" className="w-auto" aria-label="Approvers needed">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {[1, 2, 3, 4, 5].map((n) => (
                      <SelectItem key={n} value={String(n)}>
                        {n} {n === 1 ? "approver" : "approvers"}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
              <label className="flex items-center gap-2">
                <Checkbox checked={noSelf} onCheckedChange={(v) => setNoSelf(v === true)} />
                <span className="text-xs">No approving your own</span>
              </label>
            </>
          )}
          <Button size="sm" disabled={!target || !adds} onClick={() => void add()}>
            Add rule
          </Button>
        </div>
      </fieldset>
    </section>
  );
}
