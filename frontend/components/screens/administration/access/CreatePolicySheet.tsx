"use client";

import * as React from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Textarea } from "@/components/ui/textarea";
import type { PolicyEffect, ScopeKind } from "@/graphql/identity/identity.types";

import type { CreatePolicyInput } from "./use-policies";

export interface CreatePolicySheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Resolves true when the policy was created; the sheet then closes. */
  onCreate: (input: CreatePolicyInput) => Promise<boolean>;
  creating: boolean;
}

const slugify = (s: string) =>
  s
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 100);

const CONDITION_TEMPLATE = `[
  {
    "kind": "time_window",
    "days": ["mon","tue","wed","thu","fri"],
    "hours": ["09:00-18:00"],
    "tz": "America/Los_Angeles"
  }
]`;

export function CreatePolicySheet({
  open,
  onOpenChange,
  onCreate,
  creating: loading,
}: CreatePolicySheetProps) {
  const [name, setName] = React.useState("");
  const [slug, setSlug] = React.useState("");
  const [slugTouched, setSlugTouched] = React.useState(false);
  const [scopeLevel, setScopeLevel] = React.useState<ScopeKind>("ORG");
  const [effect, setEffect] = React.useState<PolicyEffect>("DENY");
  const [actionPattern, setActionPattern] = React.useState("*");
  const [conditionsText, setConditionsText] = React.useState(CONDITION_TEMPLATE);
  const [conditionsError, setConditionsError] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (!open) {
      setName("");
      setSlug("");
      setSlugTouched(false);
      setScopeLevel("ORG");
      setEffect("DENY");
      setActionPattern("*");
      setConditionsText(CONDITION_TEMPLATE);
      setConditionsError(null);
    }
  }, [open]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setConditionsError(null);
    let parsedConditions: unknown;
    try {
      parsedConditions = JSON.parse(conditionsText.trim() || "[]");
      if (!Array.isArray(parsedConditions)) {
        throw new Error("conditions must be a JSON array");
      }
    } catch (err) {
      setConditionsError(err instanceof Error ? err.message : "invalid JSON");
      return;
    }

    const created = await onCreate({
      name: name.trim(),
      slug: slug || slugify(name),
      scopeLevel,
      effect,
      actionPattern: actionPattern.trim() || "*",
      conditions: parsedConditions,
    });
    if (created) onOpenChange(false);
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex flex-col">
        <SheetHeader>
          <SheetTitle>New policy</SheetTitle>
          <SheetDescription>
            ABAC policies can only deny. The conditions array describes the runtime predicates: time
            windows, IP allowlists, MFA freshness, env match, etc.
          </SheetDescription>
        </SheetHeader>
        <form onSubmit={submit} className="flex flex-1 flex-col gap-4 overflow-y-auto px-4 pb-4">
          <div className="space-y-2">
            <Label htmlFor="name">Display name</Label>
            <Input
              id="name"
              value={name}
              onChange={(e) => {
                setName(e.target.value);
                if (!slugTouched) setSlug(slugify(e.target.value));
              }}
              placeholder="No prod deploys after hours"
              required
              autoFocus
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="slug">Slug</Label>
            <Input
              id="slug"
              value={slug}
              onChange={(e) => {
                setSlug(e.target.value);
                setSlugTouched(true);
              }}
              pattern="[a-z0-9-]+"
              required
            />
          </div>

          <div className="grid gap-3 sm:grid-cols-3">
            <div className="space-y-2">
              <Label htmlFor="scope">Scope</Label>
              <Select value={scopeLevel} onValueChange={(v) => setScopeLevel(v as ScopeKind)}>
                <SelectTrigger id="scope">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="ORG">ORG</SelectItem>
                  <SelectItem value="TEAM">TEAM</SelectItem>
                  <SelectItem value="PROJECT">PROJECT</SelectItem>
                  <SelectItem value="APP">APP</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="effect">Effect</Label>
              <Select value={effect} onValueChange={(v) => setEffect(v as PolicyEffect)}>
                <SelectTrigger id="effect">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="DENY">DENY</SelectItem>
                  <SelectItem value="ALLOW">ALLOW</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="action">Action pattern</Label>
              <Input
                id="action"
                value={actionPattern}
                onChange={(e) => setActionPattern(e.target.value)}
                placeholder="app.deploy"
                className="font-mono text-xs"
              />
            </div>
          </div>
          <p className="text-muted-foreground text-xs">
            Scope sets how widely the rule applies. Effect is normally{" "}
            <span className="font-mono">DENY</span> — ABAC narrows access, it doesn&apos;t grant it.
            Action pattern is a permission glob like <span className="font-mono">app.deploy</span>{" "}
            or <span className="font-mono">*</span> for every action.
          </p>

          <div className="space-y-2">
            <Label htmlFor="conditions">Conditions (JSON array)</Label>
            <Textarea
              id="conditions"
              value={conditionsText}
              onChange={(e) => setConditionsText(e.target.value)}
              rows={10}
              className="font-mono text-xs"
            />
            {conditionsError && <p className="text-destructive text-xs">{conditionsError}</p>}
            <p className="text-muted-foreground text-xs">
              Predicate kinds per spec/03 §5: time_window, ip_allowlist, approval_required,
              env_match, device_assertion, freshness.
            </p>
          </div>

          <SheetFooter className="mt-auto flex-row justify-end gap-2 px-0">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={loading || !name}>
              {loading ? "Creating…" : "Create policy"}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}
