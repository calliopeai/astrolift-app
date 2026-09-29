import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { SkipToContent } from "@/components/SkipToContent";

/** Visible on keyboard focus only; tab into the frame to see it. */
const meta: Meta = { title: "Patterns/Shell/SkipToContent" };
export default meta;
export const Default: StoryObj = {
  render: () => (
    <div>
      <SkipToContent />
      <main id="main-content" className="text-sm">
        Page content
      </main>
    </div>
  ),
};
