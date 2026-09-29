import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { SwatchGroup } from "@/components/foundations/Swatch";

/**
 * The design tokens (spec 44 §6): the only values a component may use. Switch
 * mode, ground and accent in the toolbar; status colours must not move.
 */
const meta: Meta = { title: "Foundations/Tokens", parameters: { layout: "padded" } };
export default meta;

export const Palette: StoryObj = {
  render: () => (
    <div className="flex flex-col gap-8">
      <SwatchGroup
        title="Surfaces"
        tokens={["background", "card", "popover", "muted", "secondary", "sidebar"]}
      />
      <SwatchGroup
        title="Text"
        tokens={[
          "foreground",
          "muted-foreground",
          "card-foreground",
          "secondary-foreground",
          "primary-foreground",
        ]}
      />
      <SwatchGroup
        title="Action and accent (follows the accent)"
        tokens={["primary", "brand-primary", "accent", "ring", "sidebar-primary"]}
      />
      <SwatchGroup
        title="Status (never follows the accent)"
        tokens={[
          "success",
          "success-fg",
          "warning",
          "warning-fg",
          "danger",
          "danger-fg",
          "info",
          "info-fg",
          "destructive",
        ]}
      />
      <SwatchGroup title="Lines" tokens={["border", "input", "sidebar-border"]} />
      <SwatchGroup
        title="Chart series"
        tokens={["chart-1", "chart-2", "chart-3", "chart-4", "chart-5"]}
      />
    </div>
  ),
};

const SCALE = [
  "text-2xs",
  "text-xs",
  "text-sm",
  "text-base",
  "text-lg",
  "text-xl",
  "text-2xl",
  "text-3xl",
];

export const Typography: StoryObj = {
  render: () => (
    <div className="flex flex-col gap-8">
      <section className="flex flex-col gap-3">
        <p className="font-head text-2xl font-semibold">DM Sans: page titles only</p>
        <p className="font-sans text-base">IBM Plex Sans: all UI and body text.</p>
        <p className="font-mono text-sm">IBM Plex Mono: ids, timestamps, counts, config, logs.</p>
      </section>
      <section className="flex flex-col gap-2">
        {SCALE.map((cls) => (
          <div key={cls} className="flex items-baseline gap-4">
            <span className="text-muted-foreground w-24 shrink-0 font-mono text-xs">{cls}</span>
            <span className={cls}>Deploy checkout to production</span>
          </div>
        ))}
      </section>
    </div>
  ),
};

const RADII = [
  "rounded-sm",
  "rounded-md",
  "rounded-lg",
  "rounded-xl",
  "rounded-2xl",
  "rounded-full",
];

export const Radius: StoryObj = {
  render: () => (
    <div className="flex flex-wrap gap-6">
      {RADII.map((cls) => (
        <div key={cls} className="flex flex-col items-center gap-2">
          <div className={`bg-muted border-border size-16 border ${cls}`} />
          <span className="text-muted-foreground font-mono text-xs">{cls}</span>
        </div>
      ))}
    </div>
  ),
};

export const Surfaces: StoryObj = {
  render: () => (
    <div className="bg-background border-border flex flex-col gap-4 rounded-lg border p-6">
      <span className="text-muted-foreground font-mono text-xs">surface-0 · background</span>
      <div className="bg-card border-border flex flex-col gap-4 rounded-lg border p-6">
        <span className="text-muted-foreground font-mono text-xs">surface-1 · card</span>
        <div className="bg-popover border-border rounded-lg border p-6 shadow-lg">
          <span className="text-muted-foreground font-mono text-xs">surface-2 · popover</span>
        </div>
      </div>
    </div>
  ),
};
