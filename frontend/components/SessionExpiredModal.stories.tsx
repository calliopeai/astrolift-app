import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { SessionExpiredModal } from "@/components/SessionExpiredModal";

const meta: Meta = { title: "Patterns/Shell/SessionExpiredModal" };
export default meta;

/** Opens on the `astrolift:session-expired` event, which the play function fires. */
export const Open: StoryObj = {
  render: () => (
    <div>
      <p className="text-muted-foreground text-sm">The dialog opens when the session expires.</p>
      <SessionExpiredModal />
    </div>
  ),
  play: async () => {
    window.dispatchEvent(new Event("astrolift:session-expired"));
  },
};
