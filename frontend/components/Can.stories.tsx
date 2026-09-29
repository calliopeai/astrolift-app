import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { Can } from "@/components/Can";
import { Button } from "@/components/ui/button";
import { PermissionsProvider } from "@/providers/PermissionsProvider";

/**
 * Permission gating. Reads the granted set from context: the toolbar's
 * Permissions switch sets it for every story; these pin one each way.
 */
const meta: Meta = { title: "Primitives/Can" };
export default meta;

const Gated = () => (
  <Can
    permission="app.delete"
    fallback={<span className="text-muted-foreground text-sm">Only an owner can delete.</span>}
  >
    <Button variant="destructive">Delete app</Button>
  </Can>
);

export const Allowed: StoryObj = {
  render: () => (
    <PermissionsProvider value={{ granted: new Set(["app.delete"]), loading: false }}>
      <Gated />
    </PermissionsProvider>
  ),
};
export const Denied: StoryObj = {
  render: () => (
    <PermissionsProvider value={{ granted: new Set(), loading: false }}>
      <Gated />
    </PermissionsProvider>
  ),
};
