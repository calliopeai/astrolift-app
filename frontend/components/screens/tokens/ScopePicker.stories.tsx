import en from "@/messages/en.json";
import { NextIntlClientProvider } from "next-intl";
import de from "@/messages/de.json";
import ja from "@/messages/ja.json";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { SCOPE_CATALOG_LONG, SCOPE_PICKER } from "@/components/screens/teams/teams-tokens.fixtures";

import { ScopePicker, type ScopePickerProps } from "./ScopePicker";

const meta: Meta = {
  decorators: [
    (Story) => (
      <NextIntlClientProvider
        locale="en"
        messages={en}
        timeZone="UTC"
        now={new Date("2026-09-30T12:00:00Z")}
      >
        <Story />
      </NextIntlClientProvider>
    ),
  ],
  title: "Screens/Tokens/ScopePicker",
};
export default meta;

type Story = StoryObj;

const NVKE_SCOPE_CATALOG = {
  scopes: [
    {
      value: "read:nvke",
      label: "Read NVKE",
      surface: "nvke",
      description: "Read NVKE sites, observations and permitted receipts in this organization.",
      sensitive: false,
      permissions: ["nvke.read"],
      available: true,
      unavailableReason: "",
    },
    {
      value: "manage:nvke",
      label: "Manage NVKE resources",
      surface: "nvke",
      description:
        "Create, update and delete native NVKE tenant resources. Select Read NVKE separately.",
      sensitive: true,
      permissions: ["nvke.manage"],
      available: true,
      unavailableReason: "",
    },
    {
      value: "admin:nvke",
      label: "Administer NVKE",
      surface: "nvke",
      description:
        "Configure NVKE authority, retrieve workload credentials and run native maintenance. Select Read NVKE separately.",
      sensitive: true,
      permissions: ["nvke.admin"],
      available: true,
      unavailableReason: "",
    },
  ],
  presets: [
    { key: "nvke_reader", label: "NVKE reader", scopes: ["read:nvke"] },
    {
      key: "nvke_operator",
      label: "NVKE operator",
      scopes: ["read:nvke", "manage:nvke", "admin:nvke"],
    },
  ],
};

export const NvkeAutomation: Story = {
  render: () => <Picker {...SCOPE_PICKER} catalog={NVKE_SCOPE_CATALOG} initial={["read:nvke"]} />,
};

export const NvkeReaderRole: Story = {
  render: () => (
    <Picker
      {...SCOPE_PICKER}
      initial={["read:nvke"]}
      catalog={{
        ...NVKE_SCOPE_CATALOG,
        scopes: NVKE_SCOPE_CATALOG.scopes.map((scope) => ({
          ...scope,
          available: scope.value === "read:nvke",
          unavailableReason:
            scope.value === "read:nvke" ? "" : "Your roles grant none of what this scope unlocks.",
        })),
      }}
    />
  ),
};

function Picker({
  initial = ["read:apps", "read:clusters", "mcp:read"],
  ...props
}: Omit<ScopePickerProps, "value" | "onChange"> & { initial?: string[] }) {
  const [value, setValue] = React.useState<string[]>(initial);
  return <ScopePicker {...props} value={value} onChange={setValue} />;
}

/** The default selection; one scope unavailable to the caller's roles. */
export const Full: Story = { render: () => <Picker {...SCOPE_PICKER} /> };

/** Admin selected: the plain warning shows. */
export const AdminSelected: Story = {
  render: () => <Picker {...SCOPE_PICKER} initial={["admin"]} />,
};

/** Nothing picked yet. */
export const Empty: Story = { render: () => <Picker {...SCOPE_PICKER} initial={[]} /> };

export const Loading: Story = {
  render: () => <Picker {...SCOPE_PICKER} catalog={undefined} loading />,
};

export const LoadFailed: Story = {
  render: () => (
    <Picker
      {...SCOPE_PICKER}
      catalog={undefined}
      error={new globalThis.Error("upstream timed out")}
    />
  ),
};

export const LongStrings: Story = {
  render: () => <Picker {...SCOPE_PICKER} catalog={SCOPE_CATALOG_LONG} />,
};

export const JapaneseAdmin: Story = {
  decorators: [
    (Story) => (
      <NextIntlClientProvider
        locale="ja"
        messages={ja}
        timeZone="Europe/Berlin"
        now={new Date("2026-09-30T12:00:00Z")}
      >
        <Story />
      </NextIntlClientProvider>
    ),
  ],
  render: () => <Picker {...SCOPE_PICKER} initial={["admin"]} />,
};
export const GermanUnavailable: Story = {
  decorators: [
    (Story) => (
      <NextIntlClientProvider
        locale="de"
        messages={de}
        timeZone="Europe/Berlin"
        now={new Date("2026-09-30T12:00:00Z")}
      >
        <Story />
      </NextIntlClientProvider>
    ),
  ],
  render: () => <Picker {...SCOPE_PICKER} />,
};
