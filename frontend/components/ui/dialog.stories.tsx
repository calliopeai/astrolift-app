import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

const meta: Meta = { title: "Atoms/Dialog" };
export default meta;

export const Open: StoryObj = {
  render: () => (
    <Dialog defaultOpen>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add a domain</DialogTitle>
          <DialogDescription>Point a hostname you own at checkout.</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline">Cancel</Button>
          <Button>Add domain</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  ),
};

/** #2125: a long unbroken value in the title stays inside the frame. */
export const LongTitle: StoryObj = {
  render: () => (
    <Dialog defaultOpen>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            Redeploy image
            sha256:1112015d8a9b7c6e5f4d3c2b1a0f9e8d7c6b5a491112015d8a9b7c6e5f4d3c2b1a0f9e8d7c6b5a49?
          </DialogTitle>
          <DialogDescription>The running pods are replaced one at a time.</DialogDescription>
        </DialogHeader>
      </DialogContent>
    </Dialog>
  ),
};
