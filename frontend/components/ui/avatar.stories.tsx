import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { Avatar, AvatarFallback, AvatarGroup, AvatarGroupCount } from "@/components/ui/avatar";

const meta: Meta = { title: "Atoms/Avatar" };
export default meta;

export const Fallbacks: StoryObj = {
  render: () => (
    <div className="flex items-center gap-4">
      <Avatar>
        <AvatarFallback>LM</AvatarFallback>
      </Avatar>
      <AvatarGroup>
        {["LM", "EH", "KK"].map((i) => (
          <Avatar key={i}>
            <AvatarFallback>{i}</AvatarFallback>
          </Avatar>
        ))}
        <AvatarGroupCount>+4</AvatarGroupCount>
      </AvatarGroup>
    </div>
  ),
};
