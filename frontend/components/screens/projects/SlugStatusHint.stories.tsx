import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import type { SlugStatus } from "./project-team-slug";
import { SlugStatusHint } from "./SlugStatusHint";

const meta: Meta = {
  title: "Screens/Projects/SlugStatusHint",
};
export default meta;

type Story = StoryObj;

const STATUSES: SlugStatus[] = ["empty", "invalid", "unchanged", "checking", "available", "taken"];

const all = (kind: "team" | "project") => (
  <div className="space-y-3 p-4">
    {STATUSES.map((s) => (
      <SlugStatusHint key={s} status={s} kind={kind} />
    ))}
  </div>
);

export const Project: Story = { render: () => all("project") };

export const Team: Story = { render: () => all("team") };
