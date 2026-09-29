import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { KeyboardShortcuts } from "@/components/KeyboardShortcuts";

/** The overlay `?` opens, listing every shortcut, the rails' `[` and `]` included. */
const meta: Meta = { title: "Shell/KeyboardShortcuts" };
export default meta;

export const Open: StoryObj = { render: () => <KeyboardShortcuts open onOpenChange={() => {}} /> };
