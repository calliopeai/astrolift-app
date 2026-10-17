import { readFileSync } from "node:fs";
import path from "node:path";

import { ApolloClient, ApolloLink, InMemoryCache, Observable } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { buildSchema, graphql, print } from "graphql";
import { NextIntlClientProvider } from "next-intl";
import type * as React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { stepUpLink } from "@/lib/apollo/links";
import { ELEVATED_EVENT, STEP_UP_EVENT } from "@/lib/auth/step-up-events";
import { ASTROLIFT_PERMISSIONS } from "@/lib/permissions/permissions.generated";
import en from "@/messages/en.json";

import { BrainFrameContainer } from "./[slug]/components/brain-frame";
import { ConnectContent } from "./[slug]/components/connect-content";
import { SettingsContent } from "./[slug]/components/settings-content";
import { SharingContent } from "./[slug]/components/sharing-content";
import { NewBrainClient } from "./new/new-brain-client";

/**
 * One path through the semantic layer screens against the real GraphQL
 * schema (#2427): create a layer and follow its creation, share it with a
 * team (through a fresh sign-in), connect to it, rebuild it, archive it and
 * restore it. The route components run with their real hooks; the server is
 * a resolver over `schema.graphql`, so every document is checked against
 * the contract as it runs.
 */

const nav = vi.hoisted(() => ({
  pathname: "/brains/new",
  search: "",
  push: vi.fn(),
  replace: vi.fn(),
}));
vi.mock("next/navigation", () => ({
  usePathname: () => nav.pathname,
  useSearchParams: () => new URLSearchParams(nav.search),
  useRouter: () => ({ push: nav.push, replace: nav.replace, prefetch: () => {} }),
}));
vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({ org: { id: ORG, slug: "acme", name: "Acme" } }),
}));
vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({
    loading: false,
    granted: new Set(ASTROLIFT_PERMISSIONS),
    can: () => true,
    cannot: () => false,
  }),
}));
const toast = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  info: vi.fn(),
  warning: vi.fn(),
}));
vi.mock("sonner", () => ({ toast }));

const ORG = "a0000000-0000-4000-8000-000000000001";
const PROJECT = "a0000000-0000-4000-8000-000000000002";
const TEAM = "a0000000-0000-4000-8000-000000000003";
const CONNECTION = "a0000000-0000-4000-8000-000000000004";
const CREATION = "a0000000-0000-4000-8000-000000000005";
const BRAIN_ID = "a0000000-0000-4000-8000-000000000006";
const DEPLOYMENT = "a0000000-0000-4000-8000-000000000007";

const schema = buildSchema(
  readFileSync(path.resolve(__dirname, "../../../schema.graphql"), "utf8")
);

const TEAM_ROW = { id: TEAM, slug: "platform", name: "Platform" };

/** The server's state for the path, and what each write was sent. */
function server() {
  const state = {
    archived: false,
    shares: [] as { team: string; access: string }[],
    stepUpsAsked: 0,
    writes: [] as { field: string; input: unknown }[],
  };
  const brain = () => ({
    id: BRAIN_ID,
    slug: "handbook-brain",
    name: "Handbook",
    kind: "mimwell",
    team: "platform",
    project: "core",
    environments: [{ name: "production", url: "https://handbook-brain.apps.example.test" }],
    sharedWith: state.shares,
    hostedApps: [],
    substrate: {
      authority: "files",
      blobs: "fs",
      vectors: "local",
      graph: "edges-table",
      cell: "authority=files, blobs=fs, vectors=local, graph=edges-table",
      persistence: [],
      replicas: 1,
      image: "",
      adopted: null,
      adoptedStore: "",
      graduationNeeded: null,
      resources: [],
    },
  });
  const ok = (data: unknown) => ({ ok: true, errors: [], data });
  const record = (field: string, input: unknown) => state.writes.push({ field, input });
  const root = {
    // Reads.
    astroliftSourceConnections: () => [
      {
        id: CONNECTION,
        kind: "github_pat",
        name: "Platform GitHub",
        displayName: "Platform GitHub",
        accountLogin: "acme",
        installationId: "",
        apiBaseUrl: "",
        oauthClientId: "",
        appClientId: "",
        needsClientId: false,
        oauthRedirectUri: "",
        repoVisibilityScopes: [],
        isOauthAppConfig: false,
        isActive: true,
        tokenExpiresAt: null,
        lastUsedAt: null,
        createdAt: "2026-10-01T00:00:00Z",
        updatedAt: "2026-10-01T00:00:00Z",
        isPersonal: false,
        userUsername: "",
        parentOauthAppId: null,
      },
    ],
    astroliftProjects: () => [
      {
        id: PROJECT,
        slug: "core",
        name: "Core",
        organization: { id: ORG, slug: "acme", name: "Acme" },
        team: TEAM_ROW,
        createdAt: "2026-10-01T00:00:00Z",
        updatedAt: "2026-10-01T00:00:00Z",
        deletedAt: null,
      },
    ],
    astroliftBrainCreation: ({ id }: { id: string }) =>
      id === CREATION
        ? {
            id: CREATION,
            slug: "handbook-brain",
            state: "READY",
            repo: "acme/handbook-brain",
            templateRef: "v1.4.0",
            commitSha: "4f1c2a9e7b3d5c8f0a1b2c3d4e5f60718293a4b5",
            deploymentId: DEPLOYMENT,
            app: brain(),
            error: null,
          }
        : null,
    astroliftApp: () => ({
      id: BRAIN_ID,
      slug: "handbook-brain",
      name: "Handbook",
      appKind: "mimwell",
      isArchived: state.archived,
      archivedAt: state.archived ? "2026-10-16T12:00:00Z" : null,
      viewerPermissions: [...ASTROLIFT_PERMISSIONS],
    }),
    astroliftBrain: () => (state.archived ? null : brain()),
    astroliftTeamsPage: () => ({
      items: [
        {
          ...TEAM_ROW,
          id: "a0000000-0000-4000-8000-000000000010",
          slug: "finance",
          name: "Finance",
          organization: { id: ORG, slug: "acme", name: "Acme" },
          createdAt: "2026-10-01T00:00:00Z",
          updatedAt: "2026-10-01T00:00:00Z",
          deletedAt: null,
        },
      ],
      nextCursor: null,
      totalCount: 1,
      page: null,
      pageSize: null,
    }),
    astroliftActionSafety: () => [],
    // Writes.
    createBrain: ({ input }: { input: unknown }) => {
      record("createBrain", input);
      return ok({
        id: CREATION,
        slug: "handbook-brain",
        state: "PENDING",
        repo: "acme/handbook-brain",
        templateRef: "v1.4.0",
        commitSha: null,
        deploymentId: null,
        app: null,
        error: null,
      });
    },
    shareBrain: ({ input }: { input: { teamSlug: string; access: string } }) => {
      record("shareBrain", input);
      // brain.share asks for a fresh sign-in the first time.
      if (state.stepUpsAsked === 0) {
        state.stepUpsAsked += 1;
        return {
          ok: false,
          errors: [
            {
              code: "STEP_UP_REQUIRED",
              message: "Share a semantic layer needs a fresh sign-in",
              field: null,
            },
          ],
          data: null,
        };
      }
      state.shares = [
        ...state.shares,
        { team: input.teamSlug, access: input.access.toLowerCase() },
      ];
      return ok(brain());
    },
    issueBrainReadToken: ({ slug }: { slug: string }) => {
      record("issueBrainReadToken", slug);
      return ok({
        id: BRAIN_ID,
        slug,
        endpoints: ["https://handbook-brain.apps.example.test"],
        header: "X-Astrolift-Brain-Token",
        token: "token-value",
        expiresAt: "2026-10-16T12:15:00Z",
      });
    },
    rebuildBrain: ({ input }: { input: unknown }) => {
      record("rebuildBrain", input);
      return ok({
        slug: "handbook-brain",
        mode: "deploy",
        deploymentId: DEPLOYMENT,
        runUrl: null,
        ingestRunUrl: null,
        rebuilds: ["the projections the brain rebuilds at deploy"],
      });
    },
    archiveBrain: ({ input }: { input: unknown }) => {
      record("archiveBrain", input);
      state.archived = true;
      return ok(brain());
    },
    restoreApp: ({ input }: { input: unknown }) => {
      record("restoreApp", input);
      state.archived = false;
      return ok({ id: BRAIN_ID, slug: "handbook-brain", isArchived: false, archivedAt: null });
    },
  };
  const responder = new ApolloLink(
    (operation) =>
      new Observable((observer) => {
        void graphql({
          schema,
          source: print(operation.query),
          variableValues: operation.variables,
          operationName: operation.operationName,
          rootValue: root,
        }).then(
          (result) => {
            // A document the schema refuses fails the test, by name.
            if (result.errors?.length) {
              observer.error(new Error(`${operation.operationName}: ${result.errors[0]!.message}`));
              return;
            }
            observer.next(result as never);
            observer.complete();
          },
          (error: unknown) => observer.error(error)
        );
      })
  );
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: ApolloLink.from([stepUpLink, responder]),
  });
  return { state, client };
}

function Screen({ client, children }: { client: ApolloClient; children: React.ReactNode }) {
  return (
    <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
      <ApolloProvider client={client}>{children}</ApolloProvider>
    </NextIntlClientProvider>
  );
}

// The app-wide prompt: the person signs in again a moment after it opens.
const signInAgain = () =>
  void setTimeout(() => window.dispatchEvent(new CustomEvent(ELEVATED_EVENT)), 20);
beforeEach(() => window.addEventListener(STEP_UP_EVENT, signInAgain));
afterEach(() => {
  window.removeEventListener(STEP_UP_EVENT, signInAgain);
  vi.clearAllMocks();
});

describe("a semantic layer, end to end (#2427)", () => {
  it("is created, shared, connected to, rebuilt, archived and restored", async () => {
    const { state, client } = server();

    // Create: the form, then the creation's progress until READY.
    nav.pathname = "/brains/new";
    nav.search = "";
    const created = render(
      <Screen client={client}>
        <NewBrainClient />
      </Screen>
    );
    const next = () => screen.getByRole("button", { name: en.brains.wizard.continue });
    fireEvent.change(await screen.findByLabelText(/Repository/), {
      target: { value: "acme/handbook-brain" },
    });
    await waitFor(() => expect(next()).toBeEnabled());
    fireEvent.click(next());
    fireEvent.change(await screen.findByLabelText(en.brains.wizard.name), {
      target: { value: "Handbook" },
    });
    fireEvent.change(screen.getByLabelText(/Slug/), { target: { value: "handbook-brain" } });
    await waitFor(() => expect(next()).toBeEnabled());
    fireEvent.click(next());
    fireEvent.click(next());
    fireEvent.click(screen.getByRole("button", { name: en.brains.wizard.create }));
    await waitFor(() => expect(nav.replace).toHaveBeenCalled());
    expect(state.writes[0]).toEqual({
      field: "createBrain",
      input: expect.objectContaining({
        projectId: PROJECT,
        slug: "handbook-brain",
        sourceConnectionId: CONNECTION,
        repo: "acme/handbook-brain",
        name: "Handbook",
        teamId: TEAM,
        deploy: true,
        substrate: expect.objectContaining({ authority: "FILES", persistence: [] }),
      }),
    });
    nav.search = new URL(nav.replace.mock.calls[0]![0] as string, "https://x.test").search;
    created.rerender(
      <Screen client={client}>
        <NewBrainClient />
      </Screen>
    );
    expect(await screen.findByText("Handbook is ready")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Open semantic layer/ })).toHaveAttribute(
      "href",
      "/brains/handbook-brain"
    );
    created.unmount();

    // Share with a team: confirmed, then a fresh sign-in, then applied.
    nav.pathname = "/brains/handbook-brain/sharing";
    nav.search = "";
    const detail = render(
      <Screen client={client}>
        <BrainFrameContainer slug="handbook-brain">
          <SharingContent />
        </BrainFrameContainer>
      </Screen>
    );
    fireEvent.click(await screen.findByRole("button", { name: "Pick Finance" }));
    fireEvent.click(screen.getByRole("button", { name: en.brains.sharing.share }));
    const confirm = within(await screen.findByRole("alertdialog"));
    fireEvent.click(confirm.getByRole("button", { name: en.brains.sharing.share }));
    await waitFor(() => expect(state.shares).toEqual([{ team: "finance", access: "viewer" }]));
    expect(state.writes.filter((w) => w.field === "shareBrain")).toHaveLength(2);
    expect(await screen.findByRole("row", { name: /finance/ })).toBeInTheDocument();

    // Connect: a token issued on the button, shown once.
    fireEvent.click(screen.getByRole("button", { name: en.brains.frame.connect }));
    expect(
      await screen.findByText("https://handbook-brain.apps.example.test/mcp")
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: en.brains.connectDialog.done }));

    // Rebuild in production.
    nav.pathname = "/brains/handbook-brain/settings";
    detail.rerender(
      <Screen client={client}>
        <BrainFrameContainer slug="handbook-brain">
          <SettingsContent />
        </BrainFrameContainer>
      </Screen>
    );
    fireEvent.click(await screen.findByRole("button", { name: en.brains.settings.rebuild }));
    expect(await screen.findByRole("link", { name: DEPLOYMENT.slice(0, 8) })).toHaveAttribute(
      "href",
      `/deployments/${DEPLOYMENT}`
    );
    expect(state.writes.at(-1)).toEqual({
      field: "rebuildBrain",
      input: { slug: "handbook-brain", environmentName: "production", ingest: false },
    });

    // Archive: confirmed; the layer turns to its archived state, which restores.
    nav.search = "?section=danger-zone";
    detail.rerender(
      <Screen client={client}>
        <BrainFrameContainer slug="handbook-brain">
          <SettingsContent />
        </BrainFrameContainer>
      </Screen>
    );
    fireEvent.click(await screen.findByRole("button", { name: en.brains.settings.archive }));
    const archive = within(await screen.findByRole("alertdialog"));
    fireEvent.click(archive.getByRole("button", { name: en.brains.settings.archive }));
    expect(await screen.findByText(en.brains.frame.archivedHeading)).toBeInTheDocument();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: en.brains.frame.restore }));
    });
    await waitFor(() =>
      expect(screen.getByRole("navigation", { name: en.brains.frame.sections })).toBeInTheDocument()
    );
    expect(state.writes.map((w) => w.field)).toEqual([
      "createBrain",
      "shareBrain",
      "shareBrain",
      "issueBrainReadToken",
      "rebuildBrain",
      "archiveBrain",
      "restoreApp",
    ]);
    // Connect never ran on its own: the only token is the one asked for.
    expect(state.writes.filter((w) => w.field === "issueBrainReadToken")).toHaveLength(1);
  }, 30_000);

  it("opens the Connect tab without issuing a token", async () => {
    const { state, client } = server();
    nav.pathname = "/brains/handbook-brain/connect";
    nav.search = "";
    render(
      <Screen client={client}>
        <BrainFrameContainer slug="handbook-brain">
          <ConnectContent />
        </BrainFrameContainer>
      </Screen>
    );
    expect(
      await screen.findByText("https://handbook-brain.apps.example.test/mcp")
    ).toBeInTheDocument();
    expect(state.writes).toEqual([]);
  });
});
