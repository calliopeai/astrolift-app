"use client";

import { HomeLayoutPicker } from "@/components/home/HomeLayoutPicker";
import type { HomeLayoutDef, HomeLayoutKey } from "@/components/home/registry";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

export interface HomeLayoutSettingsProps {
  loading?: boolean;
  /** The layouts the person is offered. */
  layouts: HomeLayoutDef[];
  /** The layout Home draws now. */
  layout: HomeLayoutDef | null;
  /** The saved choice; null means the default from access. */
  savedLayout: HomeLayoutKey | null;
  defaultLayout: HomeLayoutKey | null;
  onLayoutChange: (key: HomeLayoutKey) => void;
  onResetLayout: () => void;
}

/**
 * Settings › Home: the Home layout (spec 44 §4.3), the same question as the
 * first sign-in. A choice saves as it is made, like Visualizations. Stored in
 * this browser until the profile carries `home_layout` (#2154). Pure: the
 * layouts and the save come from useHome.
 */
export function HomeLayoutSettings({
  loading = false,
  layouts,
  layout,
  savedLayout,
  defaultLayout,
  onLayoutChange,
  onResetLayout,
}: HomeLayoutSettingsProps) {
  const defaultTitle = layouts.find((l) => l.key === defaultLayout)?.title;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Home layout</CardTitle>
        <CardDescription>
          Which panels Home shows, and in what order. Saved in this browser.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {loading ? (
          <div className="grid gap-2 md:grid-cols-2">
            <Skeleton className="h-20 w-full" />
            <Skeleton className="h-20 w-full" />
          </div>
        ) : layouts.length === 0 ? (
          <p className="text-muted-foreground text-sm">
            Your role has no access to Apps or Agents, so there is no layout to choose.
          </p>
        ) : (
          <>
            <HomeLayoutPicker
              legend="Home layout"
              layouts={layouts}
              value={layout?.key ?? null}
              onChange={onLayoutChange}
              defaultLayout={defaultLayout}
            />
            {savedLayout && defaultTitle && savedLayout !== defaultLayout && (
              <Button size="sm" variant="outline" onClick={onResetLayout}>
                Use the default for my access ({defaultTitle})
              </Button>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}
