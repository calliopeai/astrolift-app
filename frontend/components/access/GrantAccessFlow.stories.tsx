import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";
import { expect, userEvent, within } from "storybook/test";

import type { Principal } from "./access-model";
import {
  CHECKOUT,
  DANA,
  DEPLOYER,
  ENG_GROUP,
  LONG_PRINCIPAL,
  LONG_ROLE,
  LONG_SCOPE_TREE,
  PAYMENTS_TEAM,
  PREVIEW,
  PREVIEW_MANY,
  PREVIEW_REFUSED,
  PRINCIPALS,
  ROLES,
  SAM,
  SCOPE_TREE,
} from "./fixtures";
import { GrantAccessFlow, type GrantAccessFlowProps } from "./GrantAccessFlow";

/**
 * The one way to grant access, from every place: Who › Role › Scope ›
 * Expiry › Review. Four fields and a review, so a page with numbered steps
 * (spec 44 §5.4), not a sheet.
 */
const meta: Meta = { title: "Access/GrantAccessFlow", parameters: { layout: "fullscreen" } };
export default meta;

type Story = StoryObj;

const CRUMBS = [
  { label: "Admin", href: "/administration" },
  { label: "Access", href: "/administration/access" },
  { label: "Grant access" },
];

function useFakeSearch(pool: Principal[] = PRINCIPALS) {
  const [query, setQuery] = React.useState("");
  const q = query.trim().toLowerCase();
  const results = q
    ? pool.filter((p) => p.name.toLowerCase().includes(q) || p.id.toLowerCase().includes(q))
    : [];
  return { query, setQuery, results };
}

function Flow(props: Partial<GrantAccessFlowProps> & { pool?: Principal[] }) {
  const { pool, ...rest } = props;
  const search = useFakeSearch(pool);
  return (
    <GrantAccessFlow
      crumbs={CRUMBS}
      search={search}
      roles={ROLES}
      scopeTree={{ roots: SCOPE_TREE }}
      preview={() => Promise.resolve(PREVIEW)}
      onSubmit={(d) => Promise.resolve(d.principals.map((principal) => ({ principal, ok: true })))}
      onCancel={() => {}}
      onDone={() => {}}
      expirySupported
      {...rest}
    />
  );
}

const READY = {
  principals: [DANA, SAM],
  roleId: DEPLOYER.id,
  scope: CHECKOUT,
  expiry: { kind: "never" as const },
};

/** Step 1, opened from checkout's Access tab: the scope is already chosen. */
export const Who: Story = { render: () => <Flow initialDraft={{ scope: CHECKOUT }} /> };

/** Many at once: a user, a group and a team picked. */
export const WhoPicked: Story = {
  render: () => <Flow initialDraft={{ principals: [DANA, ENG_GROUP, PAYMENTS_TEAM] }} />,
};

export const WhoSearching: Story = {
  render: () => (
    <GrantAccessFlow
      crumbs={CRUMBS}
      search={{ query: "da", setQuery: () => {}, results: [DANA, ENG_GROUP] }}
      roles={ROLES}
      scopeTree={{ roots: SCOPE_TREE }}
      preview={() => Promise.resolve(PREVIEW)}
      onSubmit={() => Promise.resolve([])}
      onCancel={() => {}}
      onDone={() => {}}
    />
  ),
};

export const WhoLoading: Story = {
  render: () => (
    <GrantAccessFlow
      crumbs={CRUMBS}
      search={{ query: "dana", setQuery: () => {}, results: [], loading: true }}
      roles={ROLES}
      scopeTree={{ roots: SCOPE_TREE }}
      preview={() => Promise.resolve(PREVIEW)}
      onSubmit={() => Promise.resolve([])}
      onCancel={() => {}}
      onDone={() => {}}
    />
  ),
};

export const WhoError: Story = {
  render: () => (
    <GrantAccessFlow
      crumbs={CRUMBS}
      search={{
        query: "dana",
        setQuery: () => {},
        results: [],
        error: { message: "Network error: 502 Bad Gateway" },
      }}
      roles={ROLES}
      scopeTree={{ roots: SCOPE_TREE }}
      preview={() => Promise.resolve(PREVIEW)}
      onSubmit={() => Promise.resolve([])}
      onCancel={() => {}}
      onDone={() => {}}
    />
  ),
};

export const Role: Story = {
  render: () => <Flow initialDraft={{ ...READY, roleId: null }} initialStep={1} />,
};

export const RoleLoading: Story = {
  render: () => <Flow initialDraft={READY} initialStep={1} roles={[]} rolesLoading />,
};

/** Nothing grantable: every role offered is one the granter holds in full. */
export const RoleEmpty: Story = {
  render: () => <Flow initialDraft={READY} initialStep={1} roles={[]} />,
};

export const RoleError: Story = {
  render: () => (
    <Flow initialDraft={READY} initialStep={1} roles={[]} rolesError={{ message: "Forbidden" }} />
  ),
};

/** An app role: only apps can be picked. */
export const Scope: Story = { render: () => <Flow initialDraft={READY} initialStep={2} /> };

export const ScopeLoading: Story = {
  render: () => (
    <Flow initialDraft={READY} initialStep={2} scopeTree={{ roots: [], loading: true }} />
  ),
};

export const ScopeError: Story = {
  render: () => (
    <Flow
      initialDraft={READY}
      initialStep={2}
      scopeTree={{ roots: [], error: { message: "upstream timed out" }, onRetry: () => {} }}
    />
  ),
};

/** Never, a number of days, or a date: the grant ends by itself. */
export const Expiry: Story = { render: () => <Flow initialDraft={READY} initialStep={3} /> };

export const ExpiryOnDate: Story = {
  render: () => (
    <Flow
      initialDraft={{ ...READY, expiry: { kind: "date", date: "2026-10-31" } }}
      initialStep={3}
    />
  ),
};

/** Where an expiry cannot be set, only Never is offered. */
export const ExpiryUnsupported: Story = {
  render: () => <Flow initialDraft={READY} initialStep={3} expirySupported={false} />,
};

/** The effect, stated before anything is written. */
export const Review: Story = {
  render: () => <Flow initialDraft={READY} initialStep={4} initialPreview={PREVIEW} />,
};

/** A group grant: the lists are cut short, the counts are exact, and it reaches the group's future members. */
export const ReviewMany: Story = {
  render: () => (
    <Flow
      initialDraft={{ ...READY, principals: [ENG_GROUP] }}
      initialStep={4}
      initialPreview={PREVIEW_MANY}
    />
  ),
};

export const ReviewLoading: Story = {
  render: () => <Flow initialDraft={READY} initialStep={4} />,
};

/** Beyond the caller's grant ceiling: the review says why, and Grant stays off. */
export const ReviewRefused: Story = {
  render: () => <Flow initialDraft={READY} initialStep={4} initialPreview={PREVIEW_REFUSED} />,
};

/** Entering the review asks the preview, then shows it. */
export const ReviewAsks: Story = {
  render: () => <Flow initialDraft={READY} initialStep={3} />,
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await userEvent.click(c.getByRole("button", { name: "Continue" }));
    await expect(await c.findByTestId("grant-effect")).toHaveTextContent("2 people gain");
  },
};

/** A partly failed submit: who failed and why, in place; Retry sends only them. */
export const PartialFailure: Story = {
  render: () => (
    <Flow
      initialDraft={{ ...READY, principals: [DANA, SAM, ENG_GROUP] }}
      initialStep={4}
      initialPreview={PREVIEW}
      initialOutcomes={[
        { principal: DANA, ok: true },
        {
          principal: SAM,
          ok: false,
          error: "You cannot grant a role with permissions you do not hold.",
        },
        {
          principal: ENG_GROUP,
          ok: false,
          error: "A role binding for this group already exists at this scope.",
        },
      ]}
    />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <Flow
      pool={[LONG_PRINCIPAL]}
      roles={[LONG_ROLE]}
      scopeTree={{ roots: LONG_SCOPE_TREE }}
      initialDraft={{
        principals: [LONG_PRINCIPAL],
        roleId: LONG_ROLE.id,
        scope: { kind: "PROJECT", id: "proj-long", name: LONG_SCOPE_TREE[0].name },
        expiry: { kind: "never" },
      }}
      initialStep={4}
      initialPreview={{
        gaining: [{ principal: LONG_PRINCIPAL, permissions: LONG_ROLE.permissions }],
        already: [
          {
            principal: LONG_PRINCIPAL,
            source: { via: { kind: "group", group: LONG_PRINCIPAL.id } },
          },
        ],
      }}
    />
  ),
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <Flow initialDraft={READY} initialStep={4} initialPreview={PREVIEW} />
    </div>
  ),
};
