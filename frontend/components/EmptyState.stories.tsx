import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { BotIcon } from "lucide-react";

import { EmptyState } from "@/components/EmptyState";

/** The one empty state (spec 44 §5.1): what this is, the create action, a learn-more link. */
const meta: Meta<typeof EmptyState> = { title: "Patterns/EmptyState", component: EmptyState };
export default meta;

export const WithAction: StoryObj<typeof EmptyState> = {
  args: {
    icon: <BotIcon />,
    title: "No agents yet",
    description: "An agent runs a task with the tools and skills you give it.",
    actionHref: "#",
    actionLabel: "New agent",
    learnMoreHref: "#",
    learnMoreLabel: "How agents work",
  },
};

export const TitleOnly: StoryObj<typeof EmptyState> = {
  args: { icon: <BotIcon />, title: "Nothing here" },
};
