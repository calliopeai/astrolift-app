"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { PlusIcon, ServerIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { useForm } from "react-hook-form";

import { type Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { useCursorTableList } from "@/components/list/use-cursor-table-list";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";

import { createSiteSchema } from "./schema";
import type { NvkeCreateValues, NvkeSitesScreenProps, NvkeSiteView } from "./types";

export function NvkeSitesScreen({ table, access, pending, error, onCreate }: NvkeSitesScreenProps) {
  const t = useTranslations("nvke");
  const list = useCursorTableList(table, {
    id: "nvke.sites",
    label: t("allSites"),
    searchPlaceholder: "",
    searchable: false,
  });
  const [open, setOpen] = useState(false);
  const form = useForm<NvkeCreateValues>({
    resolver: zodResolver(createSiteSchema),
    defaultValues: { name: "", slug: "", organizationNamespace: "" },
  });
  const columns: Column<NvkeSiteView>[] = [
    {
      id: "name",
      header: t("name"),
      cellClassName: "max-w-80",
      cell: (site) => (
        <span className="block min-w-0">
          <span className="block truncate font-medium">{site.name}</span>
          <span className="text-muted-foreground block truncate font-mono text-xs">
            {site.slug}
          </span>
        </span>
      ),
    },
    {
      id: "namespace",
      header: t("namespace"),
      cellClassName: "max-w-60",
      cell: (site) => <code className="block truncate">{site.organizationNamespace}</code>,
    },
    {
      id: "status",
      header: t("state"),
      cell: (site) => (
        <Badge variant={site.isActive ? "secondary" : "outline"}>
          {t(site.isActive ? "enabled" : "disabled")}
        </Badge>
      ),
    },
    {
      id: "connections",
      header: t("interfaces"),
      cell: (site) => <span>{site.connections.filter((c) => c.isEnabled).length} / 4</span>,
    },
    { id: "version", header: t("version"), cell: (site) => site.version },
  ];
  return (
    <div className="flex min-w-0 flex-col gap-6 p-6">
      <ListPage
        header={{
          title: t("sitesTitle"),
          crumbs: [{ label: t("title") }],
          context: t("sitesDescription"),
          primaryAction: access.admin ? (
            <Button disabled={pending || access.loading} onClick={() => setOpen(true)}>
              <PlusIcon />
              {t("createSite")}
            </Button>
          ) : undefined,
        }}
        list={list}
        label={t("title")}
        rows={table.rows}
        columns={columns}
        getRowId={(site) => site.id}
        rowHref={(site) => `/nvke/${site.id}`}
        loading={table.state === "loading"}
        stale={table.isStale}
        error={table.state === "error" ? { message: t("readFailed") } : null}
        onRetry={table.retry}
        nextCursor={table.nextCursor}
        totalCount={table.totalCount}
        notice={
          error ? (
            <p role="alert" className="text-danger-fg text-sm">
              {error}
            </p>
          ) : undefined
        }
        empty={{ icon: <ServerIcon />, title: t("noSites"), description: t("noSitesHelp") }}
      />
      <Sheet
        open={open}
        onOpenChange={(next) => {
          if (!pending) {
            setOpen(next);
            if (!next) form.reset();
          }
        }}
      >
        <SheetContent>
          <SheetHeader>
            <SheetTitle>{t("createSite")}</SheetTitle>
            <SheetDescription>{t("createSiteHelp")}</SheetDescription>
          </SheetHeader>
          <form
            className="flex min-h-0 flex-col gap-4 overflow-y-auto p-4"
            onSubmit={form.handleSubmit(async (values) => {
              if (!access.admin || pending) return;
              if (await onCreate(values)) {
                form.reset();
                setOpen(false);
              }
            })}
          >
            {(["name", "slug", "organizationNamespace"] as const).map((name) => (
              <div key={name} className="space-y-2">
                <Label htmlFor={`nvke-${name}`}>
                  {t(name === "organizationNamespace" ? "namespace" : name)}
                </Label>
                <Input
                  id={`nvke-${name}`}
                  autoComplete="off"
                  disabled={pending}
                  aria-invalid={Boolean(form.formState.errors[name])}
                  {...form.register(name)}
                />
              </div>
            ))}
            {Object.keys(form.formState.errors).length > 0 && (
              <p role="alert" className="text-danger-fg text-sm">
                {t("invalidForm")}
              </p>
            )}
            <SheetFooter>
              <Button type="submit" disabled={pending || !access.admin}>
                {t(pending ? "working" : "createSite")}
              </Button>
            </SheetFooter>
          </form>
        </SheetContent>
      </Sheet>
    </div>
  );
}
