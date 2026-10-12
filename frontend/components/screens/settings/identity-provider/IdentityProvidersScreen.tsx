"use client";

import { BookOpenIcon, CheckCircle2Icon, KeyRoundIcon, PlusIcon, Trash2Icon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { selectRows } from "@/components/list/select-rows";
import type { ListStateController } from "@/components/list/list-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import type { AstroliftIdentityProvider } from "@/graphql/identity/identity.types";
import { DOC_LINKS } from "@/lib/docs/urls";
import { useFormatters } from "@/lib/i18n/formatters";

import { IDENTITY_PROVIDERS_SELECT } from "./identity-providers-list";
import type { useIdentityProviders } from "./use-identity-providers";

export type IdentityProvidersScreenProps = Omit<ReturnType<typeof useIdentityProviders>, "list"> & {
  /**
   * The "New provider" sheet. A render slot so the sheet's own hook lives
   * in a container while the open state stays with this screen.
   */
  renderCreateSheet: (props: {
    open: boolean;
    onOpenChange: (open: boolean) => void;
  }) => React.ReactNode;
};

// Kinds with a label under identityProviders.kinds; any other value renders raw.
const KIND_LABEL_KEYS = new Set([
  "oidc",
  "saml",
  "cognito",
  "auth0",
  "okta",
  "azure_ad",
  "google",
  "github",
  "local",
]);

/**
 * Providers › Identity: the org's sign-in methods as the tab's one embedded
 * list (kind and status filters, search, sort, numbered pages run over the
 * providers in hand), make active and delete in each row's `⋯`. Pure.
 */
export function IdentityProvidersScreen({
  list,
  providers,
  loading,
  error,
  canManageIdp,
  switching,
  deleting,
  onSetActive,
  onDelete,
  renderCreateSheet,
}: IdentityProvidersScreenProps & { list: ListStateController }) {
  const t = useTranslations("identityProviders");
  const [open, setOpen] = React.useState(false);
  const fmt = useFormatters();

  const [activateTarget, setActivateTarget] = React.useState<AstroliftIdentityProvider | null>(
    null
  );
  const [deleteTarget, setDeleteTarget] = React.useState<AstroliftIdentityProvider | null>(null);

  function requestSetActive(idp: AstroliftIdentityProvider) {
    if (idp.isActive) return;
    setActivateTarget(idp);
  }

  function requestDelete(idp: AstroliftIdentityProvider) {
    if (idp.isActive) return;
    setDeleteTarget(idp);
  }

  const { state } = list;
  const page = selectRows(
    providers,
    {
      filters: list.filters,
      q: state.q,
      sort: state.sort,
      page: state.page,
      pageSize: state.pageSize,
    },
    IDENTITY_PROVIDERS_SELECT
  );

  const columns: Column<AstroliftIdentityProvider>[] = [
    {
      id: "provider",
      header: t("list.columns.provider"),
      sortKey: "name",
      cellClassName: "max-w-72",
      cell: (idp) => (
        <span className="block min-w-0">
          <span className="block truncate font-medium" title={idp.name}>
            {idp.name}
          </span>
          {idp.clientId && (
            <span className="text-muted-foreground block truncate font-mono text-xs">
              {t("list.client", {
                id: idp.clientId.length > 12 ? `${idp.clientId.slice(0, 12)}…` : idp.clientId,
              })}
            </span>
          )}
        </span>
      ),
    },
    {
      id: "kind",
      header: t("list.columns.kind"),
      sortKey: "kind",
      cell: (idp) => (
        <Badge variant="outline">
          {KIND_LABEL_KEYS.has(idp.kind) ? t(`kinds.${idp.kind}`) : idp.kind}
        </Badge>
      ),
    },
    {
      id: "endpoint",
      header: t("list.columns.endpoint"),
      cellClassName: "max-w-80",
      cell: (idp) => {
        const endpoint = idp.oidcDiscoveryUrl || idp.metadataUrl || "—";
        return (
          <span className="block truncate font-mono text-xs" title={endpoint}>
            {endpoint}
          </span>
        );
      },
    },
    {
      id: "status",
      header: t("list.columns.status"),
      cell: (idp) => (
        <div className="flex min-w-0 flex-col items-start gap-1">
          {idp.isActive ? (
            <Badge className="bg-success/15 text-success-fg gap-1" variant="secondary">
              <CheckCircle2Icon className="size-3" />
              {t("list.active")}
            </Badge>
          ) : (
            <Badge variant="secondary">{t("list.configured")}</Badge>
          )}
          {idp.isActive && idp.activatedAt && (
            <span className="text-muted-foreground text-2xs">
              {t("list.activeSince", { date: fmt.formatDate(idp.activatedAt) })}
            </span>
          )}
          {idp.isActive && idp.lastSwitchedByUsername && (
            <span className="text-muted-foreground text-2xs max-w-full truncate">
              {t("list.by", { name: idp.lastSwitchedByUsername })}
            </span>
          )}
        </div>
      ),
    },
  ];

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">{t("list.title")}</h2>
          <p className="text-muted-foreground mt-1 max-w-2xl text-sm">{t("list.description")}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button asChild size="sm" variant="outline">
            <Link href={DOC_LINKS.identityProviders}>
              <BookOpenIcon className="size-4" />
              {t("list.learnMore")}
            </Link>
          </Button>
          <Can permission="org.update">
            <Button onClick={() => setOpen(true)}>
              <PlusIcon className="size-4" />
              {t("list.newProvider")}
            </Button>
          </Can>
        </div>
      </div>
      <ListPage<AstroliftIdentityProvider>
        embedded
        list={list}
        label={t("list.title")}
        columns={columns}
        rows={page.rows}
        getRowId={(idp) => idp.id}
        rowActions={
          canManageIdp
            ? (idp) => (
                <>
                  {!idp.isActive && (
                    <DropdownMenuItem disabled={switching} onSelect={() => requestSetActive(idp)}>
                      <CheckCircle2Icon className="size-4" />
                      {t("list.makeActive")}
                    </DropdownMenuItem>
                  )}
                  <DropdownMenuItem
                    variant="destructive"
                    disabled={deleting || idp.isActive}
                    onSelect={() => requestDelete(idp)}
                  >
                    <Trash2Icon className="size-4" />
                    {t("list.delete")}
                  </DropdownMenuItem>
                </>
              )
            : undefined
        }
        loading={loading}
        error={error ? { message: error.message } : null}
        totalCount={page.totalCount}
        empty={{
          icon: <KeyRoundIcon className="size-5" />,
          title: t("list.emptyTitle"),
          description: t("list.emptyDescription"),
        }}
      />

      {renderCreateSheet({ open, onOpenChange: setOpen })}

      <ConfirmDialog
        open={activateTarget !== null}
        onOpenChange={(next) => {
          if (!next) setActivateTarget(null);
        }}
        title={
          activateTarget
            ? t("list.switchTitle", { name: activateTarget.name })
            : t("list.switchTitleGeneric")
        }
        description={t("list.switchDescription")}
        confirmLabel={t("list.switchConfirm")}
        onConfirm={async () => {
          if (activateTarget) await onSetActive(activateTarget);
        }}
      />

      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(next) => {
          if (!next) setDeleteTarget(null);
        }}
        title={
          deleteTarget
            ? t("list.deleteTitle", { name: deleteTarget.name })
            : t("list.deleteTitleGeneric")
        }
        description={t("list.deleteDescription")}
        confirmLabel={t("list.deleteConfirm")}
        destructive
        onConfirm={async () => {
          if (deleteTarget) await onDelete(deleteTarget);
        }}
      />
    </div>
  );
}
