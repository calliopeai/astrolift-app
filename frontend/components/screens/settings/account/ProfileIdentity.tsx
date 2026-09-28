"use client";

import { LockIcon } from "lucide-react";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

import type { ProfileIdentityInput, useProfileIdentity } from "./use-profile-identity";

/** Settings › Profile › Identity. Pure: the profile and the save come from useProfileIdentity. */
export function ProfileIdentity({
  profile,
  loading,
  saving,
  save,
}: ReturnType<typeof useProfileIdentity>) {
  const [firstName, setFirstName] = React.useState("");
  const [lastName, setLastName] = React.useState("");
  const [email, setEmail] = React.useState("");

  React.useEffect(() => {
    if (profile) {
      setFirstName(profile.firstName);
      setLastName(profile.lastName);
      setEmail(profile.email);
    }
  }, [profile]);

  if (loading && !profile) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Identity</CardTitle>
          <CardDescription>Loading…</CardDescription>
        </CardHeader>
      </Card>
    );
  }
  if (!profile) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Identity</CardTitle>
          <CardDescription>Sign in to edit your profile.</CardDescription>
        </CardHeader>
      </Card>
    );
  }

  const locked = new Set(profile.lockedFields);
  const orgAllows = profile.orgAllowsEdit;

  // A field is editable when the org allows edits AND the IdP didn't
  // claim it. When the org disables edits entirely, the whole form
  // is read-only with the explanatory description below.
  const canEdit = (field: "first_name" | "last_name" | "email") => orgAllows && !locked.has(field);

  const dirty =
    firstName !== profile.firstName || lastName !== profile.lastName || email !== profile.email;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!dirty || !profile) return;
    const input: ProfileIdentityInput = {};
    if (canEdit("first_name") && firstName !== profile.firstName) {
      input.firstName = firstName;
    }
    if (canEdit("last_name") && lastName !== profile.lastName) {
      input.lastName = lastName;
    }
    if (canEdit("email") && email !== profile.email) {
      input.email = email;
    }
    if (Object.keys(input).length === 0) return;
    await save(input);
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Identity</CardTitle>
        <CardDescription>
          {orgAllows ? (
            <>
              Your name and email shown across the platform. Fields locked by your identity provider
              can&apos;t be edited locally — they sync from the IdP.
            </>
          ) : (
            <>
              Profile editing is disabled by your organization administrator. Ask your admin to flip{" "}
              <code>allowUserProfileEdit</code> on <code>/settings/organization</code>.
            </>
          )}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} className="grid gap-4 sm:max-w-sm">
          <div className="space-y-2">
            <Label htmlFor="username">Username</Label>
            <Input id="username" value={profile.username} readOnly className="font-mono text-xs" />
            <p className="text-muted-foreground text-2xs">
              Username is the auth identifier; it never changes.
            </p>
          </div>

          <FieldRow
            id="first-name"
            label="First name"
            value={firstName}
            onChange={setFirstName}
            disabled={!canEdit("first_name")}
            locked={locked.has("first_name")}
          />
          <FieldRow
            id="last-name"
            label="Last name"
            value={lastName}
            onChange={setLastName}
            disabled={!canEdit("last_name")}
            locked={locked.has("last_name")}
          />
          <FieldRow
            id="email"
            label="Email"
            value={email}
            onChange={setEmail}
            disabled={!canEdit("email")}
            locked={locked.has("email")}
            type="email"
          />

          {orgAllows && (
            <div>
              <Button type="submit" disabled={!dirty || saving}>
                {saving ? "Saving…" : "Save"}
              </Button>
            </div>
          )}
        </form>
      </CardContent>
    </Card>
  );
}

function FieldRow({
  id,
  label,
  value,
  onChange,
  disabled,
  locked,
  type,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (v: string) => void;
  disabled: boolean;
  locked: boolean;
  type?: string;
}) {
  return (
    <div className="space-y-2">
      <Label htmlFor={id} className="flex items-center gap-1">
        {label}
        {locked && (
          <span
            className="text-muted-foreground text-2xs inline-flex items-center gap-1"
            title="Managed by your identity provider — local edits would be overwritten on next sign-in"
          >
            <LockIcon className="size-3" />
            IdP-managed
          </span>
        )}
      </Label>
      <Input
        id={id}
        type={type ?? "text"}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
      />
    </div>
  );
}
