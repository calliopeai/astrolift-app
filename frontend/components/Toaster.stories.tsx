import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { toast } from "sonner";

import { Toaster } from "@/components/Toaster";
import { Button } from "@/components/ui/button";

/** Toasts: the outcome of a submit, never a validation error (spec 44 §5.4). */
const meta: Meta = { title: "Patterns/Feedback/Toaster" };
export default meta;

export const Kinds: StoryObj = {
  render: () => (
    <div className="flex gap-2">
      <Toaster />
      <Button variant="outline" onClick={() => toast.success("Access rule saved.")}>
        Success
      </Button>
      <Button variant="outline" onClick={() => toast.error("Save failed: upstream timed out.")}>
        Error
      </Button>
      <Button variant="outline" onClick={() => toast("Deploy queued.")}>
        Neutral
      </Button>
    </div>
  ),
};
