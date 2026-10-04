import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import {
  act,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { buildSchema, parse, validate } from "graphql";
import { readFileSync } from "node:fs";
import { NextIntlClientProvider } from "next-intl";
import type { PropsWithChildren } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import en from "@/messages/en.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import de from "@/messages/de.json";
import pt from "@/messages/pt-BR.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import zh from "@/messages/zh-Hans.json";
import { useMembershipAction } from "./use-membership-action";
import { useTeamMembers } from "@/components/screens/teams/use-team-members";
import { LIST_TEAM_MEMBERS } from "@/graphql/identity/identity.queries";
import { MEMBERS, ROLES, TEAMS } from "@/components/screens/teams/teams.fixtures";
import { TeamsClient } from "@/app/(app)/administration/access/teams/teams-client";
import { createTranslator } from "next-intl";
import { MembershipRouteClient } from "./MembershipRouteClient";
import { LegacyTeamAssignmentsClient } from "./LegacyTeamAssignmentsClient";
import { AccessLandingClient } from "./AccessLandingClient";
import { MembershipReviewPanel, MembershipSources } from "./TeamMembershipPanels";
import { row, review } from "./team-membership.fixtures";

const scope = vi.hoisted(() => ({ org: "019e0000-0000-7000-8000-000000000009", actor: "actor-A" }));
const route = vi.hoisted(() => ({
  pathname: "/administration/access/teams/engineering/members",
  params: new URLSearchParams(),
  replace: vi.fn(),
}));
vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({ org: { id: scope.org }, loading: false, error: null }),
}));
vi.mock("@/graphql/user/user.hooks", () => ({
  useMe: () => ({ user: { id: scope.actor }, loading: false, error: null }),
}));
vi.mock("next/navigation", () => ({
  usePathname: () => route.pathname,
  useSearchParams: () => route.params,
  useRouter: () => ({ replace: route.replace }),
}));
const schema = buildSchema(readFileSync("schema.graphql", "utf8"));
const locales = { en, es, fr, de, "pt-BR": pt, ja, ko, "zh-Hans": zh };
type Request = { query: string; operationName: string; variables: Record<string, unknown> };
let requests: Request[], signals: AbortSignal[], transport: (request: Request) => Promise<Response>;
const clients: ApolloClient[] = [];
function response(data: Record<string, unknown>) {
  return new Response(JSON.stringify({ data }), {
    headers: { "Content-Type": "application/json" },
  });
}
const navigation = {
  canViewTeams: true,
  canManageTeamMembers: true,
  canViewPeople: false,
  canViewRoles: false,
  canViewPolicies: false,
  canCheckAccess: false,
};
function fixture(request: Request): Response {
  if (request.operationName === "GetMyPermissions")
    return response({ astroliftMyPermissions: ["team.manage_members"] });
  if (request.operationName === "ListTeamMembers") return response({ astroliftTeamMembers: [] });
  if (request.operationName === "ListRoles") return response({ astroliftRoles: [] });
  if (request.operationName === "ListTeamsPage")
    return response({
      astroliftTeamsPage: {
        items: [{ ...TEAMS[0], ...row.team }],
        nextCursor: null,
        totalCount: 1,
        page: 1,
        pageSize: 25,
      },
    });
  if (request.operationName === "GetTeamAccessNavigation")
    return response({ me: { teamAccessNavigation: navigation } });
  if (request.operationName === "GetMembershipTeam")
    return response({ astroliftTeamMembershipTeam: row.team });
  if (request.operationName === "GetMembershipPerson")
    return response({ astroliftTeamMembershipPerson: row.person });
  if (request.operationName === "ListReviewedTeamMemberships")
    return response({
      astroliftTeamMembershipsPage: { items: [row], totalCount: 1, page: 1, pageSize: 25 },
    });
  if (request.operationName === "ListReviewedPersonTeams")
    return response({
      astroliftPersonTeamMembershipsPage: { items: [row], totalCount: 1, page: 1, pageSize: 25 },
    });
  if (request.operationName === "ListTeamMemberCandidates")
    return response({
      astroliftTeamMemberCandidatesPage: {
        items: [row.person],
        totalCount: 1,
        page: 1,
        pageSize: 25,
      },
    });
  if (request.operationName === "ListMembershipTeams")
    return response({
      astroliftMembershipTeamsPage: { items: [row.team], totalCount: 1, page: 1, pageSize: 25 },
    });
  if (request.operationName === "ListTeamMembershipRoles")
    return response({
      astroliftTeamMembershipRolesPage: {
        items: [
          {
            id: row.sources[0].roleId,
            version: 2,
            name: "Team reader",
            permissions: ["team.read"],
          },
        ],
        totalCount: 1,
        page: 1,
        pageSize: 25,
      },
    });
  if (request.operationName === "GetTeamMembershipReview")
    return response({
      astroliftTeamMembershipReview: {
        ...review,
        kind: request.variables.kind,
        expectedSource: `lease-${String(request.variables.roleId)}`,
        roles:
          request.variables.kind === "ADD"
            ? [
                {
                  id: request.variables.roleId ?? row.sources[0].roleId,
                  version: 2,
                  name: "Team reader",
                  permissions: ["team.read"],
                },
              ]
            : [],
      },
    });
  if (request.operationName === "ChangeTeamMembership") {
    const input = request.variables.input as Record<string, unknown>;
    return response({
      changeAstroliftTeamMembership: {
        ok: true,
        errors: [],
        data: {
          ...input,
          changeId: "019e0000-0000-7000-8000-000000000099",
          committed: true,
          replayed: false,
          teamMemberId: row.teamMemberId,
          removedBindingIds: [row.sources[0].id],
          remainingSources: review.remainingSources,
        },
      },
    });
  }
  throw new Error(`Unexpected operation ${request.operationName}`);
}
function provider(locale: keyof typeof locales = "en") {
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new HttpLink({
      uri: "http://control-plane.test/app/gql/config/",
      fetch: vi.fn(async (_url, options) => {
        const request = JSON.parse(String(options?.body)) as Request;
        expect(validate(schema, parse(request.query))).toEqual([]);
        requests.push(request);
        if (options?.signal) signals.push(options.signal);
        return transport(request);
      }),
    }),
  });
  clients.push(client);
  return {
    client,
    wrapper: ({ children }: PropsWithChildren) => (
      <NextIntlClientProvider locale={locale} messages={locales[locale]} timeZone="UTC">
        <ApolloProvider client={client}>{children}</ApolloProvider>
      </NextIntlClientProvider>
    ),
  };
}
function held<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
function writes() {
  return requests.filter((r) => r.operationName === "ChangeTeamMembership");
}
beforeEach(() => {
  scope.org = "019e0000-0000-7000-8000-000000000009";
  scope.actor = "actor-A";
  route.params = new URLSearchParams();
  route.replace.mockClear();
  requests = [];
  signals = [];
  transport = async (request) => fixture(request);
});
afterEach(() => {
  clients.splice(0).forEach((client) => client.stop());
});
async function reviewedRemove(
  hook: ReturnType<typeof renderHook<ReturnType<typeof useMembershipAction>, unknown>>
) {
  act(() => hook.result.current.openRemove(row));
  await waitFor(() => expect(hook.result.current.phase).toBe("review"));
}

describe("reviewed membership native HttpLink contracts", () => {
  it("does not write before selected-role review and submits the exact reviewed identity/source", async () => {
    const h = renderHook(() => useMembershipAction(vi.fn()), { wrapper: provider().wrapper });
    act(() => h.result.current.openAdd(row.team.id, row.person.orgMemberId));
    await waitFor(() => expect(h.result.current.phase).toBe("review"));
    act(() => h.result.current.onConfirm());
    expect(writes()).toHaveLength(0);
    act(() => h.result.current.onRole(row.sources[0].roleId));
    await waitFor(() =>
      expect(h.result.current.review?.expectedSource).toBe(`lease-${row.sources[0].roleId}`)
    );
    act(() => {
      h.result.current.onConfirm();
      h.result.current.onConfirm();
    });
    await waitFor(() => expect(h.result.current.phase).toBe("committed"));
    expect(writes()).toHaveLength(1);
    expect(writes()[0].variables.input).toMatchObject({
      kind: "ADD",
      teamId: row.team.id,
      orgMemberId: row.person.orgMemberId,
      roleId: row.sources[0].roleId,
      expectedSource: `lease-${row.sources[0].roleId}`,
    });
    expect(h.result.current.canClose).toBe(true);
  });
  it("keeps original UUID/source after lost reply and never repeats effects with a new tuple", async () => {
    let first = true;
    transport = async (request) => {
      if (request.operationName === "ChangeTeamMembership" && first) {
        first = false;
        throw new Error("lost reply");
      }
      return fixture(request);
    };
    const refresh = vi.fn();
    const h = renderHook(() => useMembershipAction(refresh), { wrapper: provider().wrapper });
    await reviewedRemove(h);
    act(() => h.result.current.onConfirm());
    await waitFor(() => expect(h.result.current.phase).toBe("uncertain"));
    expect(h.result.current.canClose).toBe(false);
    act(() => {
      h.result.current.close();
      h.result.current.openAdd(row.team.id, "different-person");
    });
    expect(h.result.current.target?.kind).toBe("REMOVE");
    act(() => h.result.current.onRetryOriginal());
    await waitFor(() => expect(h.result.current.phase).toBe("committed"));
    expect(writes()).toHaveLength(2);
    expect(writes()[1].variables).toEqual(writes()[0].variables);
    expect(refresh).toHaveBeenCalledTimes(1);
  });
  it("does not accept a mismatched success receipt as a committed change", async () => {
    transport = async (request) =>
      request.operationName === "ChangeTeamMembership"
        ? response({
            changeAstroliftTeamMembership: {
              ok: true,
              errors: [],
              data: {
                requestId: "foreign-request",
                teamId: row.team.id,
                orgMemberId: row.person.orgMemberId,
                changeId: "foreign",
                committed: true,
                replayed: false,
                teamMemberId: null,
                removedBindingIds: [],
                remainingSources: [],
              },
            },
          })
        : fixture(request);
    const refresh = vi.fn();
    const h = renderHook(() => useMembershipAction(refresh), { wrapper: provider().wrapper });
    await reviewedRemove(h);
    act(() => h.result.current.onConfirm());
    await waitFor(() => expect(h.result.current.phase).toBe("uncertain"));
    expect(refresh).not.toHaveBeenCalled();
  });
  it("retains committed status when roster refresh fails", async () => {
    const h = renderHook(
      () => useMembershipAction(vi.fn().mockRejectedValue(new Error("refresh denied"))),
      { wrapper: provider().wrapper }
    );
    await reviewedRemove(h);
    act(() => h.result.current.onConfirm());
    await waitFor(() => expect(h.result.current.phase).toBe("refreshFailed"));
    expect(writes()).toHaveLength(1);
    expect(h.result.current.canClose).toBe(true);
  });
  it("refuses a known stale-source envelope and requires a fresh review", async () => {
    transport = async (request) =>
      request.operationName === "ChangeTeamMembership"
        ? response({
            changeAstroliftTeamMembership: {
              ok: false,
              data: null,
              errors: [
                {
                  code: "VERSION_MISMATCH",
                  message: "Review changed grants",
                  field: null,
                  requiresAttestation: false,
                  supportedMethods: [],
                },
              ],
            },
          })
        : fixture(request);
    const h = renderHook(() => useMembershipAction(vi.fn()), { wrapper: provider().wrapper });
    await reviewedRemove(h);
    act(() => h.result.current.onConfirm());
    await waitFor(() => expect(h.result.current.phase).toBe("refused"));
    expect(h.result.current.error).toBe("Review changed grants");
    expect(h.result.current.canClose).toBe(true);
    act(() => h.result.current.onReviewAgain());
    await waitFor(() => expect(h.result.current.phase).toBe("review"));
    expect(writes()).toHaveLength(1);
  });
  it("aborts a held review and discards late responses after unmount", async () => {
    const pending = held<Response>();
    transport = async (request) =>
      request.operationName === "GetTeamMembershipReview" ? pending.promise : fixture(request);
    const h = renderHook(() => useMembershipAction(vi.fn()), { wrapper: provider().wrapper });
    act(() => h.result.current.openRemove(row));
    await waitFor(() => expect(requests).toHaveLength(1));
    h.unmount();
    expect(signals.at(-1)?.aborted).toBe(true);
    await act(async () => pending.resolve(fixture(requests[0])));
    expect(writes()).toHaveLength(0);
  });
  it("discards older role-review responses even when transport ignores cancellation", async () => {
    const old = held<Response>();
    let heldRequest: Request | undefined;
    transport = async (request) => {
      if (
        request.operationName === "GetTeamMembershipReview" &&
        request.variables.roleId === "first-role"
      ) {
        heldRequest = request;
        return old.promise;
      }
      return fixture(request);
    };
    const h = renderHook(() => useMembershipAction(vi.fn()), { wrapper: provider().wrapper });
    act(() => h.result.current.openAdd(row.team.id, row.person.orgMemberId));
    await waitFor(() => expect(h.result.current.phase).toBe("review"));
    act(() => h.result.current.onRole("first-role"));
    await waitFor(() => expect(heldRequest).toBeDefined());
    // Closing and opening starts a new review identity. The old cancelled reply cannot return it.
    act(() => h.result.current.close());
    act(() => h.result.current.openAdd(row.team.id, row.person.orgMemberId));
    await waitFor(() => expect(h.result.current.phase).toBe("review"));
    await act(async () => old.resolve(fixture(heldRequest!)));
    expect(h.result.current.roleId).toBe("");
    expect(h.result.current.review?.expectedSource).toBe("lease-null");
  });
});

describe("connected bidirectional routes and epoch admission", () => {
  it("opens exact Team and ORG Member GUID reads, exposing only team navigation", async () => {
    const { wrapper } = provider();
    render(<MembershipRouteClient direction="team" slug={row.team.slug} />, { wrapper });
    await screen.findByText(row.person.email);
    expect(
      requests.find((r) => r.operationName === "ListReviewedTeamMemberships")?.variables.teamId
    ).toBe(row.team.id);
    expect(screen.getByRole("link", { name: new RegExp(row.person.name) })).toHaveAttribute(
      "href",
      `/administration/access/people/${row.person.orgMemberId}/teams`
    );
    expect(screen.queryByRole("link", { name: "Access" })).not.toBeInTheDocument(); // the detail exposes Members, not org access management
    expect(screen.getByRole("button", { name: en.teams.memberships.addPeople })).toBeEnabled();
  });
  it("supports person→team selection with server paged roles and reviewed add", async () => {
    const { wrapper } = provider();
    render(<MembershipRouteClient direction="person" orgMemberId={row.person.orgMemberId} />, {
      wrapper,
    });
    await screen.findByText(row.team.slug);
    fireEvent.click(screen.getByRole("button", { name: en.teams.memberships.addTeam }));
    fireEvent.click(
      await screen.findByRole("button", { name: `${en.teams.memberships.choose} ${row.team.name}` })
    );
    fireEvent.click(
      await screen.findByRole("button", { name: `${en.teams.memberships.choose} Team reader` })
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: en.teams.memberships.confirmAdd })).toBeEnabled()
    );
    fireEvent.click(screen.getByRole("button", { name: en.teams.memberships.confirmAdd }));
    await screen.findByText(en.teams.memberships.committed);
    expect(writes()).toHaveLength(1);
    expect(
      requests.find((r) => r.operationName === "ListTeamMembershipRoles")?.variables
    ).toMatchObject({ teamId: row.team.id, page: 1, pageSize: 25 });
  });
  it.each(["actor", "org"] as const)(
    "tears down pending mutations on %s A→B→A and never restores old outcome",
    async (dimension) => {
      const pending = held<Response>();
      let heldRequest: Request | undefined;
      transport = async (request) => {
        if (request.operationName === "ChangeTeamMembership") {
          heldRequest = request;
          return pending.promise;
        }
        return fixture(request);
      };
      const refresh = vi.fn();
      function Context() {
        return <Action key={`${scope.actor}:${scope.org}`} />;
      }
      function Action() {
        const action = useMembershipAction(refresh);
        return (
          <div>
            <button onClick={() => action.openRemove(row)}>Open</button>
            <button onClick={action.onConfirm}>Confirm</button>
            <p>{action.phase}</p>
          </div>
        );
      }
      const { wrapper } = provider();
      const view = render(<Context />, { wrapper });
      fireEvent.click(screen.getByText("Open"));
      await screen.findByText("review");
      fireEvent.click(screen.getByText("Confirm"));
      await waitFor(() => expect(heldRequest).toBeDefined());
      const original = scope[dimension];
      scope[dimension] = "B";
      view.rerender(<Context />);
      scope[dimension] = original;
      view.rerender(<Context />);
      await act(async () => pending.resolve(fixture(heldRequest!)));
      expect(screen.getByText("reading")).toBeVisible();
      expect(screen.queryByText("committed")).not.toBeInTheDocument();
      expect(refresh).not.toHaveBeenCalled();
      expect(signals.some((signal) => signal.aborted)).toBe(true);
    }
  );
  it.each(["actor", "org"] as const)(
    "uses a new actual route epoch on %s A→B→A, with no stale roster or chooser",
    async (dimension) => {
      const old = held<Response>();
      let heldRequest: Request | undefined;
      let pause = true;
      transport = async (request) => {
        if (request.operationName === "ListReviewedTeamMemberships" && pause) {
          pause = false;
          heldRequest = request;
          return old.promise;
        }
        return fixture(request);
      };
      const { wrapper } = provider();
      const view = render(<MembershipRouteClient direction="team" slug={row.team.slug} />, {
        wrapper,
      });
      await waitFor(() => expect(heldRequest).toBeDefined());
      const original = scope[dimension];
      scope[dimension] = "B";
      view.rerender(<MembershipRouteClient direction="team" slug={row.team.slug} />);
      await screen.findByText(row.person.email);
      scope[dimension] = original;
      view.rerender(<MembershipRouteClient direction="team" slug={row.team.slug} />);
      await screen.findByText(row.person.email);
      const obsolete = {
        ...row,
        person: { ...row.person, name: "Obsolete actor roster", email: "obsolete@example.test" },
      };
      await act(async () =>
        old.resolve(
          response({
            astroliftTeamMembershipsPage: {
              items: [obsolete],
              totalCount: 1,
              page: 1,
              pageSize: 25,
            },
          })
        )
      );
      expect(screen.queryByText("obsolete@example.test")).not.toBeInTheDocument();
      expect(requests.filter((r) => r.operationName === "GetMembershipTeam")).toHaveLength(3);
    }
  );
  it.each(["actor", "org"] as const)(
    "cancels an actual roster mutation on %s ABA without restoring its receipt",
    async (dimension) => {
      const pending = held<Response>();
      let heldRequest: Request | undefined;
      transport = async (request) => {
        if (request.operationName === "ChangeTeamMembership") {
          heldRequest = request;
          return pending.promise;
        }
        return fixture(request);
      };
      const { wrapper, client } = provider();
      const component = <MembershipRouteClient direction="team" slug={row.team.slug} />;
      const view = render(component, { wrapper });
      await screen.findByText(row.person.email);
      fireEvent.pointerDown(screen.getByRole("button", { name: "People: row actions" }), {
        button: 0,
        ctrlKey: false,
        pointerType: "mouse",
      });
      fireEvent.click(await screen.findByRole("menuitem", { name: en.teams.memberships.remove }));
      fireEvent.click(
        await screen.findByRole("button", { name: en.teams.memberships.confirmRemove })
      );
      await waitFor(() => expect(heldRequest).toBeDefined());
      const original = scope[dimension];
      scope[dimension] = "B";
      view.rerender(<MembershipRouteClient direction="team" slug={row.team.slug} />);
      await screen.findByText(row.person.email);
      scope[dimension] = original;
      view.rerender(<MembershipRouteClient direction="team" slug={row.team.slug} />);
      await screen.findByText(row.person.email);
      await act(async () => pending.resolve(fixture(heldRequest!)));
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(screen.queryByText(en.teams.memberships.committed)).not.toBeInTheDocument();
      expect(writes()).toHaveLength(1);
      expect(signals.some((signal) => signal.aborted)).toBe(true);
      expect(JSON.stringify(client.cache.extract())).not.toContain("lease-");
    }
  );
  it("requires both current organization management and exact team management for legacy bulk reads", async () => {
    render(<LegacyTeamAssignmentsClient slug={row.team.slug} />, { wrapper: provider().wrapper });
    await screen.findByRole("alert");
    expect(
      requests.some((r) => r.operationName === "ListTeamMembers" || r.operationName === "ListRoles")
    ).toBe(false);
  });
  it("retains authorized legacy bulk reads with an explicit limit and no cached roster", async () => {
    transport = async (request) =>
      request.operationName === "GetTeamAccessNavigation"
        ? response({ me: { teamAccessNavigation: { ...navigation, canViewPeople: true } } })
        : request.operationName === "ListTeamMembers"
          ? response({ astroliftTeamMembers: MEMBERS })
          : fixture(request);
    const { wrapper, client } = provider();
    client.cache.writeQuery({
      query: LIST_TEAM_MEMBERS,
      variables: { teamId: row.team.id },
      data: {
        astroliftTeamMembers: [
          {
            ...MEMBERS[0],
            user: {
              ...MEMBERS[0].user,
              username: "Prior actor roster",
              email: "prior-actor@example.test",
            },
          },
        ],
      },
    });
    render(<LegacyTeamAssignmentsClient slug={row.team.slug} />, { wrapper });
    await waitFor(() =>
      expect(requests.some((r) => r.operationName === "ListTeamMembers")).toBe(true)
    );
    await waitFor(() => expect(requests.some((r) => r.operationName === "ListRoles")).toBe(true));
    expect(screen.getByText(en.teams.memberships.legacyBulkLimit)).toBeVisible();
    expect(screen.queryByText("prior-actor@example.test")).not.toBeInTheDocument();
    await screen.findByText(MEMBERS[0].user.email);
    expect(
      document.querySelector(`a[href="/administration/access/people/${MEMBERS[0].id}"]`)
    ).toBeNull();
  });
  it("preserves legacy bulk input and TEAM/ORG role constraints under fresh reads", async () => {
    transport = async (request) => {
      if (request.operationName === "ListRoles")
        return response({
          astroliftRoles: [...ROLES, { ...ROLES[0], id: "project-role", scopeLevel: "PROJECT" }],
        });
      if (request.operationName === "ListTeamMembers")
        return response({ astroliftTeamMembers: MEMBERS });
      if (request.operationName === "BulkAssignAstroliftTeamMemberRoles")
        return response({
          bulkAssignAstroliftTeamMemberRoles: {
            ok: true,
            errors: [],
            data: { assignedCount: 2, alreadyAssignedCount: 0, failedCount: 0, results: [] },
          },
        });
      return fixture(request);
    };
    const { wrapper } = provider();
    const h = renderHook(() => useTeamMembers(row.team, { fresh: true }), { wrapper });
    await waitFor(() => expect(h.result.current.roles).toHaveLength(2));
    await act(async () =>
      expect(await h.result.current.onAssign("project-role", [MEMBERS[0].id])).toBe(false)
    );
    await act(async () =>
      expect(await h.result.current.onAssign(ROLES[0].id, [MEMBERS[0].id, MEMBERS[1].id])).toBe(
        true
      )
    );
    expect(
      requests
        .filter((r) => r.operationName === "BulkAssignAstroliftTeamMemberRoles")
        .map((r) => r.variables.input)
    ).toEqual([
      { teamId: row.team.id, roleId: ROLES[0].id, memberIds: [MEMBERS[0].id, MEMBERS[1].id] },
    ]);
  });
  it("pages grantable roles before reviewing an exact role on page two", async () => {
    const distant = "019e0000-0000-7000-8000-000000000077";
    transport = async (request) => {
      if (request.operationName === "ListTeamMembershipRoles")
        return response({
          astroliftTeamMembershipRolesPage: {
            items: [
              {
                id: request.variables.page === 2 ? distant : row.sources[0].roleId,
                version: 2,
                name: request.variables.page === 2 ? "Distant role" : "Team reader",
                permissions: ["team.read"],
              },
            ],
            totalCount: 26,
            page: request.variables.page,
            pageSize: 25,
          },
        });
      return fixture(request);
    };
    render(<MembershipRouteClient direction="person" orgMemberId={row.person.orgMemberId} />, {
      wrapper: provider().wrapper,
    });
    fireEvent.click(await screen.findByRole("button", { name: en.teams.memberships.addTeam }));
    fireEvent.click(
      await screen.findByRole("button", { name: `${en.teams.memberships.choose} ${row.team.name}` })
    );
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(
      await within(dialog).findByRole("button", { name: en.shared.pagination.nextPage })
    );
    fireEvent.click(
      await within(dialog).findByRole("button", {
        name: `${en.teams.memberships.choose} Distant role`,
      })
    );
    await waitFor(() =>
      expect(
        requests.some(
          (r) => r.operationName === "GetTeamMembershipReview" && r.variables.roleId === distant
        )
      ).toBe(true)
    );
    expect(writes()).toHaveLength(0);
    expect(
      requests
        .filter((r) => r.operationName === "ListTeamMembershipRoles")
        .map((r) => r.variables.page)
        .slice(0, 2)
    ).toEqual([1, 2]);
  });
  it("Access landing chooses Teams for team managers and never redirects them to People", async () => {
    render(<AccessLandingClient />, { wrapper: provider().wrapper });
    await waitFor(() => expect(route.replace).toHaveBeenCalledWith("/administration/access/teams"));
    expect(route.replace).not.toHaveBeenCalledWith("/administration/access/people");
  });
  it.each(["actor", "org"] as const)(
    "refreshes the Teams landing on %s ABA without cached or late foreign rows",
    async (dimension) => {
      const pending = held<Response>();
      let paused = false;
      transport = async (request) => {
        if (request.operationName === "ListTeamsPage" && scope[dimension] === "B") {
          paused = true;
          return pending.promise;
        }
        return fixture(request);
      };
      const { wrapper, client } = provider();
      const view = render(<TeamsClient />, { wrapper });
      await screen.findByText(row.team.name);
      const original = scope[dimension];
      scope[dimension] = "B";
      view.rerender(<TeamsClient />);
      await waitFor(() => expect(paused).toBe(true));
      expect(screen.queryByText(row.team.name)).not.toBeInTheDocument();
      scope[dimension] = original;
      view.rerender(<TeamsClient />);
      await screen.findByText(row.team.name);
      await act(async () =>
        pending.resolve(
          response({
            astroliftTeamsPage: {
              items: [{ ...TEAMS[0], name: "Withdrawn scope team" }],
              nextCursor: null,
              totalCount: 1,
              page: 1,
              pageSize: 25,
            },
          })
        )
      );
      expect(screen.queryByText("Withdrawn scope team")).not.toBeInTheDocument();
      expect(requests.filter((r) => r.operationName === "ListTeamsPage")).toHaveLength(3);
      expect(JSON.stringify(client.cache.extract())).not.toContain("astroliftTeamsPage");
      expect(screen.getByRole("link", { name: new RegExp(row.team.name) })).toHaveAttribute(
        "href",
        `/administration/access/teams/${row.team.slug}/members`
      );
    }
  );
});

it.each(Object.entries(locales))(
  "renders exact removal and retained IdP provenance in %s without missing ICU keys",
  (locale, messages) => {
    const { wrapper } = provider(locale as keyof typeof locales);
    const copy = messages.teams.memberships;
    render(
      <MembershipReviewPanel
        review={review}
        phase="review"
        roleId=""
        error={null}
        onRole={() => {}}
        onConfirm={() => {}}
        onRetryOriginal={() => {}}
        onReviewAgain={() => {}}
      />,
      { wrapper }
    );
    expect(screen.getByRole("button", { name: copy.confirmRemove })).toBeEnabled();
    expect(screen.getByText(copy.keptOtherScopes)).toBeVisible();
    expect(screen.getByText(/Engineering group/)).toBeVisible();
    expect(screen.getByText(copy.remaining)).toBeVisible();
  }
);
it("never renders arbitrary external provenance links or unescaped source names", () => {
  render(
    <MembershipSources
      sources={[
        {
          ...row.sources[0],
          sourceHref: "https://external.test/private",
          roleName: "<script>private</script>",
        },
      ]}
    />,
    { wrapper: provider().wrapper }
  );
  expect(screen.getByText("<script>private</script>")).toBeVisible();
  expect(screen.queryByRole("link")).not.toBeInTheDocument();
  expect(document.querySelector("script")).toBeNull();
});
it.each(Object.entries(locales))(
  "describes orphan direct-grant removal without inventing a membership in %s",
  (locale, messages) => {
    const t = createTranslator({ locale, messages, namespace: "teams.memberships" });
    const { wrapper } = provider(locale as keyof typeof locales);
    render(
      <MembershipReviewPanel
        review={{ ...review, membership: { ...review.membership, teamMemberId: null } }}
        phase="review"
        roleId=""
        error={null}
        onRole={() => {}}
        onConfirm={() => {}}
        onRetryOriginal={() => {}}
        onReviewAgain={() => {}}
      />,
      { wrapper }
    );
    expect(screen.getByText(t("removeGrantsDescription", { count: 1 }))).toBeVisible();
    expect(screen.queryByText(t("removeDescription", { count: 1 }))).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: messages.teams.memberships.confirmRemove })
    ).toBeEnabled();
  }
);

it.each(Object.entries(locales))(
  "renders person membership navigation including Activity without catalogue errors in %s",
  async (locale, messages) => {
    transport = async (request) =>
      request.operationName === "GetTeamAccessNavigation"
        ? response({ me: { teamAccessNavigation: { ...navigation, canViewPeople: true } } })
        : fixture(request);
    const { client } = provider(locale as keyof typeof locales);
    const errors = vi.fn();
    render(
      <NextIntlClientProvider locale={locale} messages={messages} timeZone="UTC" onError={errors}>
        <ApolloProvider client={client}>
          <MembershipRouteClient direction="person" orgMemberId={row.person.orgMemberId} />
        </ApolloProvider>
      </NextIntlClientProvider>
    );
    await screen.findByRole("link", { name: messages.teams.memberships.activity });
    expect(
      screen.getByRole("link", { name: messages.teams.memberships.activity })
    ).toHaveAttribute("href", `/administration/access/people/${row.person.orgMemberId}/activity`);
    expect(errors).not.toHaveBeenCalled();
  }
);
