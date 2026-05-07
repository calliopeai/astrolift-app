"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { SaveIcon } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { PageShell } from "@/components/PageShell";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { UPDATE_ORGANIZATION } from "@/graphql/identity/identity.mutations";
import { LIST_ORGANIZATIONS } from "@/graphql/identity/identity.queries";
import type {
  AstroliftOrganization,
  MutationResult,
} from "@/graphql/identity/identity.types";

interface OrgsResp {
  astroliftOrganizations: AstroliftOrganization[];
}

export function OrganizationSettingsClient() {
  const orgs = useQuery<OrgsResp>(LIST_ORGANIZATIONS);
  const org = orgs.data?.astroliftOrganizations[0] ?? null;

  const [name, setName] = React.useState("");
  const [website, setWebsite] = React.useState("");
  const [auditDays, setAuditDays] = React.useState("365");
  const [previewMax, setPreviewMax] = React.useState("5");
  const [logDays, setLogDays] = React.useState("30");

  React.useEffect(() => {
    if (org) {
      setName(org.name);
      setWebsite(org.website);
      setAuditDays(String(org.auditLogRetentionDays));
      setPreviewMax(String(org.previewMaxActiveDefault));
      setLogDays(String(org.logRetentionDaysDefault));
    }
  }, [org]);

  const [updateOrg, { loading: saving }] = useMutation<{
    updateOrganization: MutationResult<AstroliftOrganization>;
  }>(UPDATE_ORGANIZATION, {
    refetchQueries: [{ query: LIST_ORGANIZATIONS }],
    awaitRefetchQueries: true,
  });

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!org) return;
    const { data } = await updateOrg({
      variables: {
        input: {
          id: org.id,
          name: name.trim(),
          website: website.trim(),
          auditLogRetentionDays: Number(auditDays) || 365,
        },
      },
    });
    if (data?.updateOrganization.ok) {
      toast.success("Organization updated");
    } else {
      toast.error(data?.updateOrganization.errors?.[0]?.message ?? "Save failed");
    }
  }

  return (
    <PageShell
      title="Organization"
      description="Edit the platform's organization-level identity and retention defaults."
    >
      {orgs.loading ? (
        <Card>
          <CardContent className="space-y-3 p-6">
            <Skeleton className="h-10 w-full max-w-md" />
            <Skeleton className="h-10 w-full max-w-md" />
            <Skeleton className="h-10 w-full max-w-md" />
          </CardContent>
        </Card>
      ) : (
        <form onSubmit={submit} className="grid gap-4">
          <Card>
            <CardHeader>
              <CardTitle>General</CardTitle>
              <CardDescription>
                Display name and homepage URL surfaced in the sidebar and emails.
              </CardDescription>
            </CardHeader>
            <CardContent className="grid gap-4 sm:max-w-lg">
              <div className="space-y-2">
                <Label htmlFor="slug">Slug</Label>
                <Input
                  id="slug"
                  value={org?.slug ?? ""}
                  disabled
                  className="font-mono"
                />
                <p className="text-muted-foreground text-xs">
                  Slug is immutable — used in URLs and namespace prefixes.
                </p>
              </div>
              <div className="space-y-2">
                <Label htmlFor="name">Display name</Label>
                <Input
                  id="name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  required
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="website">Website</Label>
                <Input
                  id="website"
                  value={website}
                  onChange={(e) => setWebsite(e.target.value)}
                  type="url"
                  placeholder="https://acme.example"
                />
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Retention defaults</CardTitle>
              <CardDescription>
                Defaults inherited by new apps. Existing apps retain their own
                values until edited explicitly.
              </CardDescription>
            </CardHeader>
            <CardContent className="grid gap-4 sm:grid-cols-3">
              <div className="space-y-2">
                <Label htmlFor="audit-days">Audit log days</Label>
                <Input
                  id="audit-days"
                  type="number"
                  min={1}
                  max={2555}
                  value={auditDays}
                  onChange={(e) => setAuditDays(e.target.value)}
                />
                <p className="text-muted-foreground text-xs">Default 365.</p>
              </div>
              <div className="space-y-2">
                <Label htmlFor="preview-max">Preview env cap</Label>
                <Input
                  id="preview-max"
                  type="number"
                  min={0}
                  value={previewMax}
                  onChange={(e) => setPreviewMax(e.target.value)}
                  disabled
                />
                <p className="text-muted-foreground text-xs">
                  Editable once the App registry surface lands.
                </p>
              </div>
              <div className="space-y-2">
                <Label htmlFor="log-days">App log days</Label>
                <Input
                  id="log-days"
                  type="number"
                  min={1}
                  value={logDays}
                  onChange={(e) => setLogDays(e.target.value)}
                  disabled
                />
                <p className="text-muted-foreground text-xs">
                  Editable once the App registry surface lands.
                </p>
              </div>
            </CardContent>
          </Card>

          <div className="flex justify-end">
            <Button type="submit" disabled={saving || !org}>
              <SaveIcon className="size-4" />
              {saving ? "Saving…" : "Save changes"}
            </Button>
          </div>
        </form>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Identity provider</CardTitle>
          <CardDescription>
            OIDC / SAML / SCIM configuration for this organization. Lands as a
            sub-route once the IdP picker is wired.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <p className="text-muted-foreground text-sm">
            Currently this dev environment uses the auth1 dev-login bypass and
            Auth0 for staging/production. The /settings/organization/identity-provider
            sub-route adds OIDC discovery, SAML metadata, and SCIM token rotation.
          </p>
        </CardContent>
      </Card>
    </PageShell>
  );
}
