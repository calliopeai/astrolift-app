"use client";

/**
 * Home (spec 44 §4.3, decisions 4 and 7): one screen, drawn from the panel
 * registry in the arrangement of the person's layout.
 *
 *   Home · Builder                                     [ Layout ▾ ]
 *   Waiting on you │ Failing │ Activity
 *   KPIs
 *   Deployments            │ Agent runs
 *
 * Each panel is its own component and fetches its own data (rule 2): Home
 * mounts only the panels the layout lists and the person may see, and
 * fetches nothing itself. A layout is a row of thirds and a row of halves,
 * so the page stays within about two screens (rule 1). Before a layout is
 * saved, the first-sign-in question takes the panels' place, so nothing
 * fetches for a layout the person is about to change. Pure.
 */

import { ChevronDownIcon, LayoutDashboardIcon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { PanelGrid, SkeletonRows } from "@/components/panel/Panel";
import { ShellHeader } from "@/components/shell/ShellHeader";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

import { HomeLayoutPicker } from "./HomeLayoutPicker";
import { localizedHomeLayout } from "./registry";
import type { HomeLayoutDef, HomeLayoutKey, HomePanelDef } from "./registry";

export const HOME_QUESTION = "What will you mostly do here?";

export interface HomeScreenProps {
  /** Access or the saved layout is still being read. */
  loading?: boolean;
  /** The layout drawn; null when the person is offered none. */
  layout: HomeLayoutDef | null;
  /** Every layout the person is offered, for the menu and the question. */
  layouts: HomeLayoutDef[];
  /** The layout's panels the person may see, in order. */
  panels: HomePanelDef[];
  /** The layout preselected from access. */
  defaultLayout: HomeLayoutKey | null;
  /** No layout saved yet: ask the one question first. */
  firstSignIn?: boolean;
  onLayoutChange: (key: HomeLayoutKey) => void;
  /** Onboarding wizard and tour, mounted around Home. */
  onboarding?: React.ReactNode;
}

export function HomeScreen({
  loading = false,
  layout,
  layouts,
  panels,
  defaultLayout,
  firstSignIn = false,
  onLayoutChange,
  onboarding,
}: HomeScreenProps) {
  const t = useTranslations("home");
  layouts = layouts.map((value) => localizedHomeLayout(value, t));
  layout = layout ? localizedHomeLayout(layout, t) : null;
  const title =
    layout && !loading && !firstSignIn ? t("titleLayout", { layout: layout.title }) : t("title");
  return (
    <div className="flex min-w-0 flex-col gap-6">
      <ShellHeader
        crumbs={[{ label: t("title") }]}
        title={title}
        primaryAction={
          !loading &&
          !firstSignIn &&
          layout && <LayoutMenu layout={layout} layouts={layouts} onLayoutChange={onLayoutChange} />
        }
      />
      {loading ? (
        <HomeSkeleton />
      ) : !layout ? (
        <EmptyState
          icon={<LayoutDashboardIcon className="size-5" />}
          title={t("noAccessTitle")}
          description={t("noAccessDescription")}
        />
      ) : firstSignIn ? (
        <FirstSignIn layouts={layouts} defaultLayout={defaultLayout} onChoose={onLayoutChange} />
      ) : (
        <PanelGrid>
          {panels.map((panel) => (
            <panel.component key={panel.key} panel={panel} />
          ))}
        </PanelGrid>
      )}
      {onboarding}
    </div>
  );
}

function LayoutMenu({
  layout,
  layouts,
  onLayoutChange,
}: {
  layout: HomeLayoutDef;
  layouts: HomeLayoutDef[];
  onLayoutChange: (key: HomeLayoutKey) => void;
}) {
  const t = useTranslations("home");
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="sm" className="gap-1.5">
          {t("layoutMenu")}
          <ChevronDownIcon className="size-3.5" aria-hidden />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-80 max-w-[calc(100vw-2rem)]">
        <DropdownMenuLabel>{t("layoutLegend")}</DropdownMenuLabel>
        <DropdownMenuRadioGroup
          value={layout.key}
          onValueChange={(key) => onLayoutChange(key as HomeLayoutKey)}
        >
          {layouts.map((l) => (
            <DropdownMenuRadioItem key={l.key} value={l.key} className="items-start py-1.5">
              <span className="min-w-0">
                <span className="block font-medium">{l.title}</span>
                <span className="text-muted-foreground block text-xs [overflow-wrap:anywhere]">
                  {l.description}
                </span>
              </span>
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
        <DropdownMenuSeparator />
        <DropdownMenuItem asChild>
          <Link href="/settings/home">{t("settings")}</Link>
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** The one first-sign-in question, with the access default preselected. */
function FirstSignIn({
  layouts,
  defaultLayout,
  onChoose,
}: {
  layouts: HomeLayoutDef[];
  defaultLayout: HomeLayoutKey | null;
  onChoose: (key: HomeLayoutKey) => void;
}) {
  const t = useTranslations("home");
  const [picked, setPicked] = React.useState<HomeLayoutKey | null>(
    defaultLayout ?? layouts[0]?.key ?? null
  );
  return (
    <section className="bg-card min-w-0 rounded-md border p-4 md:p-6">
      <HomeLayoutPicker
        legend={t("question")}
        showLegend
        layouts={layouts}
        value={picked}
        onChange={setPicked}
        defaultLayout={defaultLayout}
      />
      <div className="mt-4 flex min-w-0 flex-wrap items-center justify-between gap-3">
        <p className="text-muted-foreground text-xs">{t("changeHint")}</p>
        <Button size="sm" disabled={!picked} onClick={() => picked && onChoose(picked)}>
          {t("continue")}
        </Button>
      </div>
    </section>
  );
}

const SKELETON_SPANS = [
  "xl:col-span-4",
  "xl:col-span-4",
  "xl:col-span-4",
  "xl:col-span-6",
  "xl:col-span-6",
];

function HomeSkeleton() {
  return (
    <PanelGrid>
      {SKELETON_SPANS.map((span, i) => (
        <div
          key={i}
          aria-hidden
          className={`bg-card col-span-12 min-w-0 rounded-md border p-4 ${span}`}
        >
          <SkeletonRows />
        </div>
      ))}
    </PanelGrid>
  );
}
