import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { ViewToggle } from "@/components/ViewToggle";
import type { ViewMode } from "@/hooks/use-view-toggle";

/** List or cards (spec 44 §6: density is a real axis). */
const meta: Meta = { title: "Patterns/ViewToggle" };
export default meta;

function Demo() {
  const [mode, setMode] = React.useState<ViewMode>("list");
  return <ViewToggle mode={mode} onChange={setMode} />;
}
export const Default: StoryObj = { render: () => <Demo /> };
