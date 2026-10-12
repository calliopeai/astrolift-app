"use client";

import { SettingsIcon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";

import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { type SelectRowsSpec, selectRows } from "@/components/list/select-rows";
import {
  type ListDefinition,
  standardViews,
  useLocalListState,
} from "@/components/list/use-list-state";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";

import type { EnvVar, Group, Source } from "./configuration-groups";

/** One variable with the group it is documented under. */
interface VarRow extends EnvVar {
  groupId: string;
  groupTitle: string;
  /** Position in the reference, so the default order is the document's. */
  order: number;
}

const varKey = (v: VarRow) => `${v.groupId}:${v.name}`;

function docLink(href: string) {
  return function DocLink(chunks: React.ReactNode) {
    return (
      <Link href={href} className="text-foreground underline-offset-2 hover:underline">
        {chunks}
      </Link>
    );
  };
}

const SOURCES: Source[] = ["operator", "image", "platform"];

type Translate = (key: string) => string;

function configurationList(groups: Group[], t: Translate): ListDefinition {
  return {
    id: "documentation.configuration",
    fields: [
      {
        key: "group",
        label: t("filters.group"),
        options: groups.map((g) => ({ value: g.id, label: g.title })),
      },
      {
        key: "source",
        label: t("filters.source"),
        options: SOURCES.map((value) => ({ value, label: t(`source.${value}`) })),
      },
      {
        key: "required",
        label: t("filters.required"),
        options: [
          { value: "yes", label: t("yes") },
          { value: "no", label: t("no") },
        ],
      },
    ],
    searchPlaceholder: t("searchPlaceholder"),
    defaultSort: [{ key: "order", dir: "asc" }],
    views: standardViews({ owner: "me" }, [], {
      mineNote: t("mineNote"),
    }),
    paging: "numbered",
    pageSizes: [25, 50, 100],
    defaultPageSize: 50,
  };
}

const CONFIGURATION_SELECT: SelectRowsSpec<VarRow> = {
  filter: {
    owner: () => false,
    group: (v, value) => v.groupId === value,
    source: (v, value) => v.source === value,
    required: (v, value) => v.required === (value === "yes"),
  },
  text: (v) => [v.name, v.purpose, v.default, v.groupTitle],
  sort: {
    order: (v) => v.order,
    name: (v) => v.name,
    group: (v) => v.groupTitle.toLowerCase(),
    required: (v) => (v.required ? 0 : 1),
    source: (v) => v.source,
  },
  id: varKey,
};

function sourceBadge(source: Source, t: Translate) {
  switch (source) {
    case "image":
      return <Badge variant="outline">{t("source.image")}</Badge>;
    case "platform":
      return <Badge variant="secondary">{t("source.platform")}</Badge>;
    case "operator":
    default:
      return <Badge variant="outline">{t("source.operator")}</Badge>;
  }
}

export interface ConfigurationScreenProps {
  groups: Group[];
}

/**
 * The static "Configuration" documentation page: every env var in one list,
 * filterable by the group it is documented under, with the groups' intros
 * above it.
 */
export function ConfigurationScreen({ groups }: ConfigurationScreenProps) {
  const t = useTranslations("docsConfiguration");
  const [def] = React.useState(() => configurationList(groups, t));
  const list = useLocalListState(def);
  const rows: VarRow[] = [];
  for (const g of groups) {
    for (const v of g.vars) {
      rows.push({ ...v, groupId: g.id, groupTitle: g.title, order: rows.length });
    }
  }
  const page = selectRows(
    rows,
    {
      filters: list.filters,
      q: list.state.q,
      sort: list.state.sort,
      page: list.state.page,
      pageSize: list.state.pageSize,
    },
    CONFIGURATION_SELECT
  );

  const columns: Column<VarRow>[] = [
    {
      id: "name",
      header: t("columns.variable"),
      sortKey: "name",
      width: "w-1/4",
      cellClassName: "align-top font-mono text-xs [overflow-wrap:anywhere]",
      cell: (v) => v.name,
    },
    {
      id: "purpose",
      header: t("columns.purpose"),
      cellClassName: "text-muted-foreground align-top text-xs leading-relaxed",
      cell: (v) => v.purpose,
    },
    {
      id: "group",
      header: t("columns.group"),
      sortKey: "group",
      width: "w-32",
      cellClassName: "text-muted-foreground align-top text-xs",
      cell: (v) => v.groupTitle,
    },
    {
      id: "default",
      header: t("columns.default"),
      width: "w-32",
      cellClassName: "text-muted-foreground align-top font-mono text-xs [overflow-wrap:anywhere]",
      cell: (v) => v.default ?? t("noDefault"),
    },
    {
      id: "required",
      header: t("columns.required"),
      sortKey: "required",
      width: "w-20",
      cellClassName: "align-top",
      cell: (v) =>
        v.required ? (
          <Badge className="bg-warning/15 text-warning-fg" variant="secondary">
            {t("yes")}
          </Badge>
        ) : (
          <span className="text-muted-foreground text-xs">{t("no")}</span>
        ),
    },
    {
      id: "source",
      header: t("columns.source"),
      sortKey: "source",
      width: "w-32",
      cellClassName: "align-top",
      cell: (v) => sourceBadge(v.source, t),
    },
  ];

  const showGroup = (id: string) => {
    list.setFilter("group", id);
    document.getElementById("variables")?.scrollIntoView({ block: "start" });
  };

  return (
    <article className="flex max-w-4xl flex-1 flex-col gap-6 p-6">
      <div>
        <h1 className="text-2xl font-semibold">{t("title")}</h1>
        <p className="text-muted-foreground mt-2 text-sm">
          {t.rich("intro", {
            operator: t("source.operator"),
            image: t("source.image"),
            platform: t("source.platform"),
            outline: (chunks) => (
              <Badge variant="outline" className="mx-0.5">
                {chunks}
              </Badge>
            ),
            secondary: (chunks) => (
              <Badge variant="secondary" className="mx-0.5">
                {chunks}
              </Badge>
            ),
          })}
        </p>
      </div>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">{t("conventionsTitle")}</h2>
        <ul className="text-muted-foreground flex flex-col gap-2 text-sm">
          <li>
            {t.rich("conventionBoolean", {
              values: "true / false / 1 / 0 / yes / no / on / off",
              code: (chunks) => <code>{chunks}</code>,
            })}
          </li>
          <li>
            {t.rich("conventionRequired", {
              code: (chunks) => <code>{chunks}</code>,
              strong: (chunks) => <strong className="text-foreground">{chunks}</strong>,
            })}
          </li>
          <li>{t("conventionUnlisted")}</li>
        </ul>
      </section>

      {groups.length > 0 && (
        <section className="flex flex-col gap-3">
          <h2 className="text-lg font-medium">{t("groupsTitle")}</h2>
          <dl className="flex min-w-0 flex-col gap-3 text-sm">
            {groups.map((g) => (
              <div key={g.id} id={g.id} className="min-w-0">
                <dt>
                  <button
                    type="button"
                    onClick={() => showGroup(g.id)}
                    className="text-foreground text-left font-medium [overflow-wrap:anywhere] underline-offset-2 hover:underline"
                  >
                    {g.title}
                  </button>
                  <span className="text-muted-foreground ml-2 font-mono text-xs tabular-nums">
                    {g.vars.length}
                  </span>
                </dt>
                <dd className="text-muted-foreground mt-0.5 leading-relaxed [overflow-wrap:anywhere]">
                  {g.intro}
                </dd>
              </div>
            ))}
          </dl>
        </section>
      )}

      <section id="variables" className="flex min-w-0 flex-col gap-3">
        <h2 className="text-lg font-medium">{t("variablesTitle")}</h2>
        <ListPage<VarRow>
          embedded
          list={list}
          label={t("variablesTitle")}
          columns={columns}
          rows={page.rows}
          getRowId={varKey}
          totalCount={page.totalCount}
          empty={{
            icon: <SettingsIcon className="size-5" />,
            title: t("emptyTitle"),
          }}
        />
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">{t("inspectTitle")}</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          {t.rich("inspectBody", { code: (chunks) => <code>{chunks}</code> })}
        </p>
        <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
          <code>{`./run.sh manage features
./run.sh manage diffsettings`}</code>
        </pre>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">{t("relatedTitle")}</h2>
        <ul className="text-muted-foreground flex flex-col gap-1.5 text-sm">
          <li>{t.rich("relatedGetStarted", { link: docLink("/documentation/get-started") })}</li>
          <li>
            {t.rich("relatedIdentityProviders", {
              link: docLink("/documentation/identity-providers"),
            })}
          </li>
          <li>
            {t.rich("relatedClusterPrerequisites", {
              link: docLink("/documentation/cluster-prerequisites"),
            })}
          </li>
        </ul>
      </section>
    </article>
  );
}
