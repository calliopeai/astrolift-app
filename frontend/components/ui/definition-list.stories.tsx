import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { DefinitionList } from "@/components/ui/definition-list";

const meta: Meta<typeof DefinitionList> = {
  title: "Atoms/DefinitionList",
  component: DefinitionList,
};
export default meta;

const ITEMS = [
  { term: "Cluster", description: "conflict-astrolift" },
  { term: "Region", description: "us-west-2" },
  { term: "Image", description: "registry.example.com/checkout@sha256:4f1c9e0a4f1c9e0a" },
];

export const Row: StoryObj<typeof DefinitionList> = { args: { items: ITEMS, orientation: "row" } };
export const Stack: StoryObj<typeof DefinitionList> = {
  args: { items: ITEMS, orientation: "stack" },
};
