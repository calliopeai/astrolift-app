import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import {
  CommandPalette,
  PAGES,
  type PaletteEntry,
  QUICK_ACTIONS,
} from "@/components/CommandPalette";

/** ⌘K: jump to any page or app by name (spec 44 §4). */
const meta: Meta = { title: "Shell/CommandPalette" };
export default meta;

const APPS: PaletteEntry[] = [
  { label: "checkout", href: "/apps/checkout", group: "Apps", hint: "checkout" },
  { label: "billing-api", href: "/apps/billing-api", group: "Apps", hint: "billing-api" },
];

export const Open: StoryObj = {
  render: () => (
    <CommandPalette
      open
      onOpenChange={() => {}}
      entries={[...QUICK_ACTIONS, ...APPS, ...PAGES]}
      onNavigate={() => {}}
    />
  ),
};
