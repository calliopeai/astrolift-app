"use client";

import { SettingsIcon } from "lucide-react";
import Link from "next/link";
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

const SOURCE_OPTIONS: { value: Source; label: string }[] = [
  { value: "operator", label: "operator" },
  { value: "image", label: "image" },
  { value: "platform", label: "platform-injected" },
];

function configurationList(groups: Group[]): ListDefinition {
  return {
    id: "documentation.configuration",
    fields: [
      {
        key: "group",
        label: "Group",
        options: groups.map((g) => ({ value: g.id, label: g.title })),
      },
      { key: "source", label: "Source", options: SOURCE_OPTIONS },
      {
        key: "required",
        label: "Required",
        options: [
          { value: "yes", label: "yes" },
          { value: "no", label: "no" },
        ],
      },
    ],
    searchPlaceholder: "Search variables, purposes, defaults…",
    defaultSort: [{ key: "order", dir: "asc" }],
    views: standardViews({ owner: "me" }, [], {
      mineNote: "The reference is the same for everyone, so Mine is empty.",
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

function sourceBadge(source: Source) {
  switch (source) {
    case "image":
      return <Badge variant="outline">image</Badge>;
    case "platform":
      return <Badge variant="secondary">platform-injected</Badge>;
    case "operator":
    default:
      return <Badge variant="outline">operator</Badge>;
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
  const [def] = React.useState(() => configurationList(groups));
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
      header: "Variable",
      sortKey: "name",
      width: "w-1/4",
      cellClassName: "align-top font-mono text-xs [overflow-wrap:anywhere]",
      cell: (v) => v.name,
    },
    {
      id: "purpose",
      header: "Purpose",
      cellClassName: "text-muted-foreground align-top text-xs leading-relaxed",
      cell: (v) => v.purpose,
    },
    {
      id: "group",
      header: "Group",
      sortKey: "group",
      width: "w-32",
      cellClassName: "text-muted-foreground align-top text-xs",
      cell: (v) => v.groupTitle,
    },
    {
      id: "default",
      header: "Default",
      width: "w-32",
      cellClassName: "text-muted-foreground align-top font-mono text-xs [overflow-wrap:anywhere]",
      cell: (v) => v.default ?? "none",
    },
    {
      id: "required",
      header: "Required",
      sortKey: "required",
      width: "w-20",
      cellClassName: "align-top",
      cell: (v) =>
        v.required ? (
          <Badge className="bg-warning/15 text-warning-fg" variant="secondary">
            yes
          </Badge>
        ) : (
          <span className="text-muted-foreground text-xs">no</span>
        ),
    },
    {
      id: "source",
      header: "Source",
      sortKey: "source",
      width: "w-32",
      cellClassName: "align-top",
      cell: (v) => sourceBadge(v.source),
    },
  ];

  const showGroup = (id: string) => {
    list.setFilter("group", id);
    document.getElementById("variables")?.scrollIntoView({ block: "start" });
  };

  return (
    <article className="flex max-w-4xl flex-1 flex-col gap-6 p-6">
      <div>
        <h1 className="text-2xl font-semibold">Configuration</h1>
        <p className="text-muted-foreground mt-2 text-sm">
          Every environment variable Astrolift reads at runtime. Source ={" "}
          <Badge variant="outline" className="mx-0.5">
            operator
          </Badge>{" "}
          means the operator sets it,{" "}
          <Badge variant="outline" className="mx-0.5">
            image
          </Badge>{" "}
          means the container image bakes a default in, and{" "}
          <Badge variant="secondary" className="mx-0.5">
            platform-injected
          </Badge>{" "}
          means Astrolift writes it into the workload at deploy time.
        </p>
      </div>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Conventions</h2>
        <ul className="text-muted-foreground flex flex-col gap-2 text-sm">
          <li>
            Boolean values accept <code>true / false / 1 / 0 / yes / no / on / off</code>{" "}
            (case-insensitive).
          </li>
          <li>
            Vars marked <strong className="text-foreground">required</strong> either have no default
            or have a default that&apos;s only safe for local development (e.g.{" "}
            <code>not-a-secret</code>).
          </li>
          <li>Anything not listed here is not read by Astrolift — silently ignored if set.</li>
        </ul>
      </section>

      {groups.length > 0 && (
        <section className="flex flex-col gap-3">
          <h2 className="text-lg font-medium">Groups</h2>
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
        <h2 className="text-lg font-medium">Variables</h2>
        <ListPage<VarRow>
          embedded
          list={list}
          label="Variables"
          columns={columns}
          rows={page.rows}
          getRowId={varKey}
          totalCount={page.totalCount}
          empty={{
            icon: <SettingsIcon className="size-5" />,
            title: "No variables documented",
          }}
        />
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">How to inspect what&apos;s set</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          The Django management command <code>features</code> prints the current feature-flag state,
          and <code>python manage.py diffsettings</code> shows every Django setting against its
          default. The control plane never echoes secret values — settings marked sensitive print as{" "}
          <code>***</code>.
        </p>
        <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
          <code>{`./run.sh manage features
./run.sh manage diffsettings`}</code>
        </pre>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Related</h2>
        <ul className="text-muted-foreground flex flex-col gap-1.5 text-sm">
          <li>
            <Link
              href="/documentation/get-started"
              className="text-foreground underline-offset-2 hover:underline"
            >
              Get started
            </Link>{" "}
            — bring the stack up locally; env vars there default to the docker-compose service
            names.
          </li>
          <li>
            <Link
              href="/documentation/identity-providers"
              className="text-foreground underline-offset-2 hover:underline"
            >
              Identity providers
            </Link>{" "}
            — the AUTH0_* / ASTROLIFT_IDP_* vars are described in detail.
          </li>
          <li>
            <Link
              href="/documentation/cluster-prerequisites"
              className="text-foreground underline-offset-2 hover:underline"
            >
              Cluster prerequisites
            </Link>{" "}
            — what the tenant cluster needs separately from these env vars.
          </li>
        </ul>
      </section>
    </article>
  );
}
