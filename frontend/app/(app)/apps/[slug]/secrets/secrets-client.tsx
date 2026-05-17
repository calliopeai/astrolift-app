"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  KeyIcon,
  PackageIcon,
  PlusIcon,
  Trash2Icon,
  UploadIcon,
} from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
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
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import type { MutationResult } from "@/graphql/identity/identity.types";
import { LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftAppEnvironment } from "@/graphql/lifecycle/lifecycle.types";
import {
  BULK_IMPORT_APP_SECRETS,
  DELETE_APP_SECRET,
  SET_APP_SECRET,
} from "@/graphql/services/services.mutations";
import {
  LIST_APP_SECRETS,
  LIST_APP_SECRET_BUNDLE_ATTACHMENTS,
} from "@/graphql/services/services.queries";

import { AppTabs } from "../components/app-tabs";

const ALL_ENVS = "__all__";

interface AppSecret {
  id: string;
  key: string;
  environmentName: string;
  source: string;
  bundleSlug: string;
  managedServiceKind: string;
  isMasked: boolean;
  lastEditedAt?: string | null;
}

interface SecretsResp {
  astroliftAppSecrets: AppSecret[];
}

interface EnvsResp {
  astroliftEnvironments: AstroliftAppEnvironment[];
}

const SOURCE_LABEL: Record<string, string> = {
  literal: "literal",
  bundle: "bundle",
  managed_service: "managed",
};

const SOURCE_TONE: Record<string, "secondary" | "outline" | "default"> = {
  literal: "secondary",
  bundle: "outline",
  managed_service: "default",
};

export function SecretsClient({ slug }: { slug: string }) {
  const t = useTranslations("apps.secrets");
  const [envName, setEnvName] = React.useState<string>(ALL_ENVS);
  const [setOpen, setSetOpen] = React.useState(false);
  const [bulkOpen, setBulkOpen] = React.useState(false);
  const [deleteTarget, setDeleteTarget] = React.useState<AppSecret | null>(null);

  const variables = {
    appSlug: slug,
    environmentName: envName === ALL_ENVS ? null : envName,
  };

  const envs = useQuery<EnvsResp>(LIST_ENVIRONMENTS, {
    variables: { appSlug: slug },
  });
  const secrets = useQuery<SecretsResp>(LIST_APP_SECRETS, {
    variables,
    fetchPolicy: "cache-and-network",
  });

  const refetch = [
    { query: LIST_APP_SECRETS, variables },
    {
      query: LIST_APP_SECRET_BUNDLE_ATTACHMENTS,
      variables,
    },
  ];

  const [setSecret, setState] = useMutation<{
    setAppSecret: MutationResult<{
      appSlug: string;
      key: string;
      rawManifestStaged: string;
    }>;
  }>(SET_APP_SECRET, { refetchQueries: refetch, awaitRefetchQueries: true });
  const [deleteSecret, deleteState] = useMutation<{
    deleteAppSecret: MutationResult<{
      appSlug: string;
      key: string;
      rawManifestStaged: string;
    }>;
  }>(DELETE_APP_SECRET, { refetchQueries: refetch, awaitRefetchQueries: true });
  const [bulkImport, bulkState] = useMutation<{
    bulkImportAppSecrets: MutationResult<{
      appSlug: string;
      keysSet: string[];
      rawManifestStaged: string;
    }>;
  }>(BULK_IMPORT_APP_SECRETS, {
    refetchQueries: refetch,
    awaitRefetchQueries: true,
  });

  const busy = setState.loading || deleteState.loading || bulkState.loading;
  const list = secrets.data?.astroliftAppSecrets ?? [];
  const envList = envs.data?.astroliftEnvironments ?? [];

  function requestDelete(s: AppSecret) {
    if (s.source !== "literal") {
      toast.error(
        s.source === "bundle"
          ? "Detach the bundle to remove this key."
          : "Managed-service envelope keys aren't editable directly.",
      );
      return;
    }
    setDeleteTarget(s);
  }

  async function handleDelete(s: AppSecret) {
    const { data } = await deleteSecret({
      variables: { input: { appSlug: slug, key: s.key } },
    });
    if (data?.deleteAppSecret.ok) {
      toast.success(`Deleted ${s.key}`);
    } else {
      throw new Error(data?.deleteAppSecret.errors?.[0]?.message ?? "Delete failed");
    }
  }

  return (
    <PageShell
      title={t("title")}
      description={
        <span className="text-muted-foreground font-mono text-xs">
          {t("description", { slug })}
        </span>
      }
      actions={
        <>
          <Can permission="app.deploy">
            <Button variant="outline" onClick={() => setBulkOpen(true)}>
              <UploadIcon className="size-4" />
              {t("bulkImport")}
            </Button>
          </Can>
          <Can permission="app.deploy">
            <Button onClick={() => setSetOpen(true)}>
              <PlusIcon className="size-4" />
              {t("newSecret")}
            </Button>
          </Can>
        </>
      }
    >
      <AppTabs slug={slug} active="secrets" />

      <div className="flex flex-wrap items-center gap-2">
        <Label className="text-xs uppercase tracking-wide text-muted-foreground">
          {t("environment")}
        </Label>
        <Select value={envName} onValueChange={setEnvName}>
          <SelectTrigger className="w-56">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL_ENVS}>{t("allEnvironments")}</SelectItem>
            {envList.map((e) => (
              <SelectItem key={e.id} value={e.name}>
                {e.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <span className="text-muted-foreground text-xs">
          {t("keysCount", { count: list.length })}
        </span>
      </div>

      <Card>
        <CardContent className="p-0">
          {secrets.loading && list.length === 0 ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : list.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<KeyIcon className="size-5" />}
                title={t("emptyTitle")}
                description={t("emptyDescription")}
              />
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("columns.key")}</TableHead>
                  <TableHead>{t("columns.source")}</TableHead>
                  <TableHead>{t("columns.env")}</TableHead>
                  <TableHead>{t("columns.lastEdited")}</TableHead>
                  <TableHead className="w-12 text-right"></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {list.map((s) => (
                  <TableRow key={s.id}>
                    <TableCell className="font-mono text-xs">{s.key}</TableCell>
                    <TableCell>
                      <Badge variant={SOURCE_TONE[s.source] ?? "outline"}>
                        {SOURCE_LABEL[s.source] ?? s.source}
                      </Badge>
                      {s.bundleSlug && (
                        <span className="text-muted-foreground ml-2 font-mono text-[11px]">
                          {s.bundleSlug}
                        </span>
                      )}
                      {s.managedServiceKind && (
                        <span className="text-muted-foreground ml-2 font-mono text-[11px]">
                          {s.managedServiceKind}
                        </span>
                      )}
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline" className="font-mono text-[10px]">
                        {s.environmentName || "—"}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-muted-foreground text-xs">
                      {s.lastEditedAt
                        ? new Date(s.lastEditedAt).toLocaleString()
                        : "—"}
                    </TableCell>
                    <TableCell className="text-right">
                      {s.source === "literal" && (
                        <Can permission="app.deploy">
                          <Button
                            variant="ghost"
                            size="icon"
                            className="size-8"
                            onClick={() => requestDelete(s)}
                            disabled={busy}
                          >
                            <Trash2Icon className="size-4" />
                            <span className="sr-only">Delete</span>
                          </Button>
                        </Can>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <SetSecretSheet
        open={setOpen}
        onOpenChange={setSetOpen}
        slug={slug}
        onSubmit={async (key, value) => {
          const { data } = await setSecret({
            variables: { input: { appSlug: slug, key, value } },
          });
          if (data?.setAppSecret.ok) {
            toast.success(`Set ${key}`);
            setSetOpen(false);
            return true;
          }
          toast.error(data?.setAppSecret.errors?.[0]?.message ?? "Save failed");
          return false;
        }}
        busy={busy}
      />

      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(next) => {
          if (!next) setDeleteTarget(null);
        }}
        title={
          deleteTarget
            ? t("delete.title", { key: deleteTarget.key })
            : t("delete.fallbackTitle")
        }
        description={t("delete.description")}
        confirmLabel={t("delete.confirm")}
        destructive
        onConfirm={async () => {
          if (deleteTarget) await handleDelete(deleteTarget);
        }}
      />

      <BulkImportSheet
        open={bulkOpen}
        onOpenChange={setBulkOpen}
        onSubmit={async (dotenvText) => {
          const { data } = await bulkImport({
            variables: { input: { appSlug: slug, dotenvText } },
          });
          if (data?.bulkImportAppSecrets.ok) {
            const keys = data.bulkImportAppSecrets.data?.keysSet ?? [];
            toast.success(`Imported ${keys.length} key${keys.length === 1 ? "" : "s"}`);
            setBulkOpen(false);
            return true;
          }
          toast.error(
            data?.bulkImportAppSecrets.errors?.[0]?.message ?? "Import failed",
          );
          return false;
        }}
        busy={busy}
      />
    </PageShell>
  );
}

function SetSecretSheet({
  open,
  onOpenChange,
  slug: _slug,
  onSubmit,
  busy,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  slug: string;
  onSubmit: (key: string, value: string) => Promise<boolean>;
  busy: boolean;
}) {
  const t = useTranslations("apps.secrets.setSheet");
  const tCommon = useTranslations("apps.common");
  const [key, setKey] = React.useState("");
  const [value, setValue] = React.useState("");

  React.useEffect(() => {
    if (!open) {
      setKey("");
      setValue("");
    }
  }, [open]);

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex flex-col">
        <SheetHeader>
          <SheetTitle>{t("title")}</SheetTitle>
          <SheetDescription>{t("description")}</SheetDescription>
        </SheetHeader>
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            if (!key.trim() || !value) return;
            await onSubmit(key.trim(), value);
          }}
          className="flex flex-1 flex-col gap-4 px-4 pb-4"
        >
          <div className="space-y-2">
            <Label htmlFor="secret-key">{t("keyLabel")}</Label>
            <Input
              id="secret-key"
              value={key}
              onChange={(e) => setKey(e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, "_"))}
              placeholder="DATABASE_URL"
              autoFocus
              required
              spellCheck={false}
              className="font-mono"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="secret-value">{t("valueLabel")}</Label>
            <Input
              id="secret-value"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              type="password"
              required
              spellCheck={false}
              className="font-mono"
            />
            <p className="text-muted-foreground text-xs">{t("valueHint")}</p>
          </div>
          <SheetFooter className="mt-auto flex-row justify-end gap-2 px-0">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              {tCommon("cancel")}
            </Button>
            <Button type="submit" disabled={busy || !key || !value}>
              {busy ? t("submitting") : t("submit")}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}

function BulkImportSheet({
  open,
  onOpenChange,
  onSubmit,
  busy,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (dotenvText: string) => Promise<boolean>;
  busy: boolean;
}) {
  const t = useTranslations("apps.secrets.bulkSheet");
  const tCommon = useTranslations("apps.common");
  const [text, setText] = React.useState("");

  React.useEffect(() => {
    if (!open) setText("");
  }, [open]);

  const previewKeys = React.useMemo(() => {
    return text
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => line && !line.startsWith("#"))
      .map((line) => line.split("=")[0]?.trim())
      .filter((k): k is string => Boolean(k));
  }, [text]);

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex flex-col sm:max-w-xl">
        <SheetHeader>
          <SheetTitle>{t("title")}</SheetTitle>
          <SheetDescription>{t("description")}</SheetDescription>
        </SheetHeader>
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            if (!text.trim()) return;
            await onSubmit(text);
          }}
          className="flex flex-1 flex-col gap-4 px-4 pb-4"
        >
          <Textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={16}
            spellCheck={false}
            className="font-mono text-xs"
            placeholder={"DATABASE_URL=postgres://...\nREDIS_URL=redis://..."}
          />
          {previewKeys.length > 0 && (
            <div className="rounded border bg-muted/30 p-3 text-xs">
              <p className="text-muted-foreground mb-2">
                {t("willSet", { count: previewKeys.length })}
              </p>
              <div className="flex flex-wrap gap-1">
                {previewKeys.slice(0, 20).map((k) => (
                  <Badge key={k} variant="outline" className="font-mono text-[10px]">
                    {k}
                  </Badge>
                ))}
                {previewKeys.length > 20 && (
                  <Badge variant="outline" className="font-mono text-[10px]">
                    +{previewKeys.length - 20}
                  </Badge>
                )}
              </div>
            </div>
          )}
          <SheetFooter className="mt-auto flex-row justify-end gap-2 px-0">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              {tCommon("cancel")}
            </Button>
            <Button type="submit" disabled={busy || !text.trim()}>
              {busy ? t("submitting") : t("submit")}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}

// PackageIcon will be used in the upcoming "Attach bundle" sheet —
// keep imported so the next iteration doesn't have to re-shuffle.
void PackageIcon;
