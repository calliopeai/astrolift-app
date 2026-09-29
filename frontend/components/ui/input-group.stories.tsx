import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { SearchIcon } from "lucide-react";

import {
  InputGroup,
  InputGroupAddon,
  InputGroupInput,
  InputGroupText,
} from "@/components/ui/input-group";

const meta: Meta = { title: "Atoms/InputGroup" };
export default meta;

export const Search: StoryObj = {
  render: () => (
    <div className="flex max-w-sm flex-col gap-3">
      <InputGroup>
        <InputGroupAddon>
          <SearchIcon />
        </InputGroupAddon>
        <InputGroupInput placeholder="Search runs, ids…" aria-label="Search" />
        <InputGroupAddon align="inline-end">
          <InputGroupText className="font-mono">/</InputGroupText>
        </InputGroupAddon>
      </InputGroup>
      <InputGroup>
        <InputGroupAddon>
          <InputGroupText>https://</InputGroupText>
        </InputGroupAddon>
        <InputGroupInput placeholder="checkout.astro.example.com" aria-label="Hostname" />
      </InputGroup>
    </div>
  ),
};
