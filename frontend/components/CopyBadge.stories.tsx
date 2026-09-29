import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { CopyBadge } from "@/components/CopyBadge";

const meta: Meta<typeof CopyBadge> = { title: "Patterns/CopyBadge", component: CopyBadge };
export default meta;

export const Default: StoryObj<typeof CopyBadge> = {
  args: { value: "pr-142.checkout.astro.example.com", openHref: "#" },
};

export const TruncatedLabel: StoryObj<typeof CopyBadge> = {
  args: { value: "conflict-astrolift-namespace-storefront", label: "conflict-astrolift…" },
};
