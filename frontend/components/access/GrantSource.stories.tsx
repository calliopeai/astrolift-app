import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { NextIntlClientProvider } from "next-intl";
import ja from "@/messages/ja.json";

import { GrantSource } from "./GrantSource";
import { LONG } from "./fixtures";

/**
 * Why a principal has a grant where it is being looked at. Each part links
 * to the place the grant can be removed.
 */
const meta: Meta<typeof GrantSource> = {
  title: "Access/GrantSource",
  component: GrantSource,
  parameters: { layout: "padded" },
  args: { source: {} },
};
export default meta;

type Story = StoryObj<typeof GrantSource>;

export const Direct: Story = {};

export const ViaGroup: Story = {
  args: {
    source: {
      via: {
        kind: "group",
        group: "okta:eng",
        href: "/administration/access/people/group:okta:eng",
      },
    },
  },
};

export const ViaTeam: Story = {
  args: {
    source: {
      via: { kind: "team", team: "payments", href: "/administration/access/teams/payments" },
    },
  },
};

export const InheritedFromOrg: Story = {
  args: {
    source: {
      inheritedFrom: { kind: "ORG", id: "org-acme", name: "acme", href: "/administration/access" },
    },
  },
};

/** A group grant made on the project, seen from one of its apps. */
export const ViaGroupAndInherited: Story = {
  args: {
    source: {
      via: { kind: "group", group: "okta:eng" },
      inheritedFrom: {
        kind: "PROJECT",
        id: "proj-storefront",
        name: "storefront",
        href: "/projects/storefront",
      },
    },
  },
};

export const LongStrings: Story = {
  render: () => (
    <div className="max-w-xs border p-2">
      <GrantSource
        source={{
          via: { kind: "group", group: `azuread:${LONG}`, href: "/x" },
          inheritedFrom: { kind: "TEAM", id: "t", name: LONG, href: "/y" },
        }}
      />
    </div>
  ),
};

/** In a table cell beside a name, at 768px. */
export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="flex flex-col gap-2 overflow-hidden border p-4">
      {[
        {},
        { via: { kind: "team" as const, team: "payments" } },
        { inheritedFrom: { kind: "ORG" as const, id: "o", name: "acme" } },
        {
          via: { kind: "group" as const, group: `okta:${LONG}` },
          inheritedFrom: { kind: "PROJECT" as const, id: "p", name: LONG },
        },
      ].map((source, i) => (
        <div key={i} className="flex min-w-0 items-center gap-3">
          <span className="w-32 shrink-0 truncate text-sm">Dana Reyes</span>
          <GrantSource source={source} />
        </div>
      ))}
    </div>
  ),
};

export const JapaneseInherited: Story = {
  ...ViaGroupAndInherited,
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="ja" messages={ja}>
        <Story />
      </NextIntlClientProvider>
    ),
  ],
};
