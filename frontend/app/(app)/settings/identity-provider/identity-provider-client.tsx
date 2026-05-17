"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  AlertTriangleIcon,
  BookOpenIcon,
  CheckCircle2Icon,
  KeyRoundIcon,
  PlusIcon,
  Trash2Icon,
} from "lucide-react";
import Link from "next/link";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  SET_ACTIVE_IDENTITY_PROVIDER,
  SOFT_DELETE_IDENTITY_PROVIDER,
} from "@/graphql/identity/identity.mutations";
import { LIST_IDENTITY_PROVIDERS } from "@/graphql/identity/identity.queries";
import { DOC_LINKS } from "@/lib/docs/urls";
import type { AstroliftIdentityProvider, MutationResult } from "@/graphql/identity/identity.types";
import { useFormatters } from "@/lib/i18n/formatters";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

import { CreateIdentityProviderDialog } from "./create-identity-provider-dialog";

interface Resp {
  astroliftIdentityProviders: AstroliftIdentityProvider[];
}

const KIND_LABEL: Record<string, string> = {
  oidc: "Generic OIDC",
  saml: "SAML 2.0",
  cognito: "Amazon Cognito",
  auth0: "Auth0",
  okta: "Okta",
  azure_ad: "Azure AD / Entra ID",
  google: "Google Workspace",
  github: "GitHub OAuth",
  local: "Local accounts",
};

export function IdentityProviderClient() {
  const [open, setOpen] = React.useState(false);
  const { can } = useMyPermissions();
  const canManageIdp = can("org.update");
  const fmt = useFormatters();
  const { data, loading, error } = useQuery<Resp>(LIST_IDENTITY_PROVIDERS);

  const [setActive, { loading: switching }] = useMutation<{
    setActiveIdentityProvider: MutationResult<AstroliftIdentityProvider>;
  }>(SET_ACTIVE_IDENTITY_PROVIDER, {
    refetchQueries: [{ query: LIST_IDENTITY_PROVIDERS }],
    awaitRefetchQueries: true,
  });

  const [softDelete, { loading: deleting }] = useMutation<{
    softDeleteIdentityProvider: MutationResult<{ id: string; deleted: boolean }>;
  }>(SOFT_DELETE_IDENTITY_PROVIDER, {
    refetchQueries: [{ query: LIST_IDENTITY_PROVIDERS }],
    awaitRefetchQueries: true,
  });

  const [activateTarget, setActivateTarget] = React.useState<AstroliftIdentityProvider | null>(
    null
  );
  const [deleteTarget, setDeleteTarget] = React.useState<AstroliftIdentityProvider | null>(null);

  function requestSetActive(idp: AstroliftIdentityProvider) {
    if (idp.isActive) return;
    setActivateTarget(idp);
  }

  function requestDelete(idp: AstroliftIdentityProvider) {
    if (idp.isActive) {
      toast.error("Cannot delete the active provider — set a different one first.");
      return;
    }
    setDeleteTarget(idp);
  }

  async function handleSetActive(idp: AstroliftIdentityProvider) {
    const { data } = await setActive({ variables: { input: { id: idp.id } } });
    if (data?.setActiveIdentityProvider.ok) {
      toast.success(`Active provider: ${idp.name}`);
    } else {
      throw new Error(data?.setActiveIdentityProvider.errors?.[0]?.message ?? "Failed");
    }
  }

  async function handleDelete(idp: AstroliftIdentityProvider) {
    const { data } = await softDelete({ variables: { input: { id: idp.id } } });
    if (data?.softDeleteIdentityProvider.ok) {
      toast.success(`Deleted ${idp.name}`);
    } else {
      throw new Error(data?.softDeleteIdentityProvider.errors?.[0]?.message ?? "Failed");
    }
  }

  const list = data?.astroliftIdentityProviders ?? [];

  return (
    <PageShell
      title="Identity providers"
      description="Sign-in methods configured for the organization. Auth0, generic OIDC, Cognito, Okta, Azure AD, Google, GitHub, SAML, or local accounts. Exactly one is active at a time."
      actions={
        <>
          <Button asChild size="sm" variant="outline">
            <Link href={DOC_LINKS.identityProviders}>
              <BookOpenIcon className="size-4" />
              Learn more
            </Link>
          </Button>
          <Can permission="org.update">
            <Button onClick={() => setOpen(true)}>
              <PlusIcon className="size-4" />
              New provider
            </Button>
          </Can>
        </>
      }
    >
      {error && (
        <Card className="border-destructive/40 bg-destructive/5">
          <CardHeader className="flex flex-row items-start gap-3 space-y-0 pb-3">
            <AlertTriangleIcon className="text-destructive mt-0.5 size-4" />
            <div className="flex-1">
              <CardTitle className="text-destructive text-sm">
                Couldn&apos;t load identity providers
              </CardTitle>
              <CardDescription>{error.message}</CardDescription>
            </div>
          </CardHeader>
        </Card>
      )}

      <Card>
        <CardContent className="p-0">
          {loading ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : list.length === 0 && !error ? (
            <div className="p-6">
              <EmptyState
                icon={<KeyRoundIcon className="size-5" />}
                title="No identity providers configured"
                description="Configure at least one provider so users can sign in. Local accounts are useful for first-run; production installs typically wire OIDC or Cognito."
                actionHref={undefined}
              />
            </div>
          ) : list.length === 0 ? null : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Provider</TableHead>
                  <TableHead>Kind</TableHead>
                  <TableHead>Endpoint</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {list.map((idp) => (
                  <TableRow key={idp.id}>
                    <TableCell>
                      <div className="font-medium">{idp.name}</div>
                      {idp.clientId && (
                        <div className="text-muted-foreground font-mono text-xs">
                          client {idp.clientId.slice(0, 12)}…
                        </div>
                      )}
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline">{KIND_LABEL[idp.kind] ?? idp.kind}</Badge>
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      {idp.oidcDiscoveryUrl || idp.metadataUrl || "—"}
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-col items-start gap-1">
                        {idp.isActive ? (
                          <Badge
                            className="gap-1 bg-emerald-500/15 text-emerald-700 dark:text-emerald-300"
                            variant="secondary"
                          >
                            <CheckCircle2Icon className="size-3" />
                            active
                          </Badge>
                        ) : (
                          <Badge variant="secondary">configured</Badge>
                        )}
                        {idp.isActive && (idp.activatedAt || idp.updatedAt) && (
                          // Prefer the dedicated `activatedAt` stamp
                          // (set only when the IdP is flipped to
                          // active); fall back to `updatedAt` for
                          // legacy rows from before #467.
                          <span className="text-muted-foreground text-[10px]">
                            Active since{" "}
                            {fmt.formatDate((idp.activatedAt ?? idp.updatedAt) as string)}
                          </span>
                        )}
                        {idp.isActive && idp.lastSwitchedByUsername && (
                          <span className="text-muted-foreground text-[10px]">
                            by {idp.lastSwitchedByUsername}
                          </span>
                        )}
                      </div>
                    </TableCell>
                    <TableCell className="text-right">
                      {canManageIdp ? (
                        <div className="flex justify-end gap-1">
                          {!idp.isActive && (
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() => requestSetActive(idp)}
                              disabled={switching}
                            >
                              Make active
                            </Button>
                          )}
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => requestDelete(idp)}
                            disabled={deleting || idp.isActive}
                          >
                            <Trash2Icon className="size-4" />
                            <span className="sr-only">Delete</span>
                          </Button>
                        </div>
                      ) : null}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <CreateIdentityProviderDialog open={open} onOpenChange={setOpen} />

      <ConfirmDialog
        open={activateTarget !== null}
        onOpenChange={(next) => {
          if (!next) setActivateTarget(null);
        }}
        title={
          activateTarget
            ? `Switch active provider to ${activateTarget.name}?`
            : "Switch active provider?"
        }
        description="Users already signed in keep their sessions. The next sign-in routes through the new provider. If users only exist in the old provider, they'll be unable to log in until provisioned here."
        confirmLabel="Switch provider"
        onConfirm={async () => {
          if (activateTarget) await handleSetActive(activateTarget);
        }}
      />

      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(next) => {
          if (!next) setDeleteTarget(null);
        }}
        title={deleteTarget ? `Delete ${deleteTarget.name}?` : "Delete identity provider?"}
        description="Soft-deletes the IdP record. Users whose identity exists only in this provider can no longer sign in — provision them in the active provider first if they need continued access."
        confirmLabel="Delete provider"
        destructive
        onConfirm={async () => {
          if (deleteTarget) await handleDelete(deleteTarget);
        }}
      />
    </PageShell>
  );
}
