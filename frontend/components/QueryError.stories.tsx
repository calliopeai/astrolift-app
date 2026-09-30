import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { QueryError } from "./QueryError";
const meta: Meta<typeof QueryError> = { title: "Patterns/QueryError", component: QueryError };
export default meta;
export const Failed: StoryObj<typeof QueryError> = {
  args: {
    title: "Could not load environments",
    error: { message: "Permission denied" },
    onRetry: () => {},
  },
};
export const LongMessage: StoryObj<typeof QueryError> = {
  args: {
    ...Failed.args,
    error: { message: "Upstream request failed: " + "unavailable/".repeat(40) },
  },
};
