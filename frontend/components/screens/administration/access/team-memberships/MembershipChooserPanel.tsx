"use client";

import { useTranslations } from "next-intl";
import { UsersIcon } from "lucide-react";
import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import type { ListStateController } from "@/components/list/list-state";
import { Button } from "@/components/ui/button";

export interface MembershipChoice {
  id: string;
  name: string;
  detail: string;
}
export function MembershipChooserPanel({
  direction,
  kind,
  list,
  rows,
  totalCount,
  loading,
  error,
  onRetry,
  onChoose,
}: {
  direction: "team" | "person";
  kind?: "role";
  list: ListStateController;
  rows: MembershipChoice[];
  totalCount: number;
  loading: boolean;
  error: { message: string } | null;
  onRetry: () => void;
  onChoose: (row: MembershipChoice) => void;
}) {
  const t = useTranslations("teams.memberships");
  const columns: Column<MembershipChoice>[] = [
    {
      id: "name",
      header: t(kind === "role" ? "role" : direction === "team" ? "people" : "teams"),
      cell: (row) => (
        <div className="min-w-0 [overflow-wrap:anywhere]">
          {row.name}
          <p className="text-muted-foreground text-xs">{row.detail}</p>
        </div>
      ),
    },
    {
      id: "choose",
      header: t("choose"),
      cell: (row) => (
        <Button
          variant="outline"
          onClick={() => onChoose(row)}
          aria-label={`${t("choose")} ${row.name}`}
        >
          {t("choose")}
        </Button>
      ),
    },
  ];
  return (
    <ListPage
      embedded
      list={list}
      label={t(kind === "role" ? "role" : direction === "team" ? "people" : "teams")}
      columns={columns}
      rows={rows}
      getRowId={(row) => row.id}
      totalCount={totalCount}
      loading={loading}
      error={error}
      onRetry={onRetry}
      empty={{ icon: <UsersIcon className="size-5" />, title: t("empty") }}
    />
  );
}
