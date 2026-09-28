import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { TagInput } from "@/components/ui/tag-input";

const meta: Meta = { title: "Atoms/TagInput" };
export default meta;

function Demo({ initial }: { initial: string[] }) {
  const [tags, setTags] = React.useState(initial);
  return (
    <div className="max-w-md">
      <TagInput
        value={tags}
        onChange={setTags}
        suggestions={["veruus", "ops", "sales"]}
        placeholder="Add a group"
      />
    </div>
  );
}

export const Default: StoryObj = { render: () => <Demo initial={["veruus", "ops"]} /> };
export const Empty: StoryObj = { render: () => <Demo initial={[]} /> };
