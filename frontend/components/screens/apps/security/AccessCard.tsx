"use client";

import { useTranslations } from "next-intl";
import { DoorOpenIcon, XIcon } from "lucide-react";
import * as React from "react";

import { Can } from "@/components/Can";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import type { AstroliftAppAccess } from "@/graphql/__generated__/schema";

import type { useAccessEditor, useAppAccess } from "./use-app-access";

export type AccessCardViewProps = ReturnType<typeof useAppAccess> & {
  /** The editor, shown only to an operator holding `app.access`. */
  editor?: React.ReactNode;
};

/**
 * Who may enter this app behind central auth (#2132). Enforced at the Envoy
 * edge: a user outside the list signs in and gets a page saying they have no
 * access, never the app. An app whose astrolift.toml declares
 * [ingress.access] is managed there, and this card only shows it.
 */
export function AccessCardView({ access, loading, editor }: AccessCardViewProps) {
  const t = useTranslations("apps.security.access");
  if (loading && !access) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">{t("title")}</CardTitle>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-20 w-full" />
        </CardContent>
      </Card>
    );
  }
  if (!access) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-sm">
          <DoorOpenIcon className="size-4" />
          {t("title")}
          {access.managedByManifest && (
            <Badge variant="outline" className="text-2xs font-normal">
              {t("managedByManifest")}
            </Badge>
          )}
        </CardTitle>
        <CardDescription>
          {access.restricted ? t("restrictedDescription") : t("openDescription")}{" "}
          {access.enforcedOn.length === 0 && t("notEnforced")}
        </CardDescription>
      </CardHeader>
      <CardContent>
        {access.managedByManifest ? (
          <AccessSummary access={access} />
        ) : (
          <Can permission="app.access" fallback={<AccessSummary access={access} />}>
            {editor}
          </Can>
        )}
      </CardContent>
    </Card>
  );
}

function AccessSummary({ access }: { access: AstroliftAppAccess }) {
  const t = useTranslations("apps.security.access");
  if (!access.restricted) return <p className="text-muted-foreground text-sm">{t("noRule")}</p>;
  return (
    <dl className="grid gap-2 text-sm">
      <div>
        <dt className="text-muted-foreground text-xs">{t("groups")}</dt>
        <dd className="flex flex-wrap gap-1">
          {access.groups.length ? access.groups.map((g) => <Badge key={g}>{g}</Badge>) : "—"}
        </dd>
      </div>
      <div>
        <dt className="text-muted-foreground text-xs">{t("users")}</dt>
        <dd className="flex flex-wrap gap-1">
          {access.users.length
            ? access.users.map((u) => (
                <Badge key={u} variant="secondary" className="[overflow-wrap:anywhere]">
                  {u}
                </Badge>
              ))
            : "—"}
        </dd>
      </div>
    </dl>
  );
}

function ListInput({
  id,
  label,
  values,
  onChange,
  placeholder,
  validate,
}: {
  id: string;
  label: string;
  values: string[];
  onChange: (next: string[]) => void;
  placeholder: string;
  validate?: (v: string) => string | null;
}) {
  const t = useTranslations("apps.security.access");
  const [draft, setDraft] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  function add() {
    const v = draft.trim();
    if (!v) return;
    const problem = validate?.(v) ?? null;
    if (problem) {
      setError(problem);
      return;
    }
    if (!values.includes(v)) onChange([...values, v]);
    setDraft("");
    setError(null);
  }
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <div className="flex flex-wrap gap-1">
        {values.map((v) => (
          <Badge key={v} variant="secondary" className="gap-1 [overflow-wrap:anywhere]">
            {v}
            <button
              type="button"
              aria-label={t("remove", { value: v })}
              onClick={() => onChange(values.filter((x) => x !== v))}
            >
              <XIcon className="size-3" />
            </button>
          </Badge>
        ))}
      </div>
      <div className="flex gap-2">
        <Input
          id={id}
          value={draft}
          placeholder={placeholder}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              add();
            }
          }}
        />
        <Button type="button" variant="outline" size="sm" onClick={add}>
          {t("add")}
        </Button>
      </div>
      {error && <p className="text-destructive text-xs">{error}</p>}
    </div>
  );
}

export type AccessEditorViewProps = ReturnType<typeof useAccessEditor>;

export function AccessEditorView({
  groups,
  setGroups,
  users,
  setUsers,
  changed,
  preview: p,
  saving,
  onSave,
}: AccessEditorViewProps) {
  const t = useTranslations("apps.security.access");
  return (
    <form
      className="space-y-4"
      onSubmit={async (e) => {
        e.preventDefault();
        await onSave();
      }}
    >
      <ListInput
        id="access-groups"
        label={t("groups")}
        values={groups}
        onChange={setGroups}
        placeholder="veruus"
      />
      <ListInput
        id="access-users"
        label={t("usersEmail")}
        values={users}
        onChange={setUsers}
        placeholder="someone@example.com"
        validate={(v) => (v.includes("@") ? null : t("invalidEmail"))}
      />
      {changed && p && p.total != null && p.allowed != null && (
        <p role="status" className="text-muted-foreground text-xs">
          {t("preview", { allowed: p.allowed, total: p.total })}
          {p.losing.length > 0 && (
            <span className="text-warning-fg">
              {" "}
              {t("losingAccess", {
                count: p.losing.length,
                users: p.losing.slice(0, 5).join(", "),
              })}
              {p.losing.length > 5 ? "…" : ""}
            </span>
          )}
        </p>
      )}
      <div className="flex justify-end">
        <Button type="submit" size="sm" disabled={!changed || saving}>
          {saving ? t("saving") : t("save")}
        </Button>
      </div>
    </form>
  );
}
