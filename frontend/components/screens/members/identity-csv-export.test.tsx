import { MockedProvider } from "@apollo/client/testing/react";
import type { MockedResponse } from "@apollo/client/testing";
import { act, renderHook, waitFor } from "@testing-library/react";
import { GraphQLError } from "graphql";
import type { ReactNode } from "react";
import { NextIntlClientProvider } from "next-intl";
import messages from "@/messages/en.json";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  EXPORT_INVITATIONS_CSV,
  EXPORT_MEMBERS_CSV,
  EXPORT_ROLE_BINDINGS_CSV,
  LIST_INVITATIONS_PAGE,
  LIST_MEMBERS_PAGE,
  LIST_ROLE_BINDINGS_PAGE,
  LIST_ROLES,
} from "@/graphql/identity/identity.queries";
import { PRINCIPAL_SEARCH } from "@/graphql/access/access.queries";

import { useAssignments } from "../administration/permissions/use-assignments";
import { useMembers } from "./use-members";

const state = vi.hoisted(() => ({ url: "", download: vi.fn(), error: vi.fn() }));
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(state.url),
  usePathname: () => "/administration/access/people",
  useRouter: () => ({ replace: vi.fn() }),
}));
vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({ can: () => true }),
}));
vi.mock("sonner", () => ({ toast: { error: state.error, success: vi.fn() } }));
vi.mock("@/components/list/exportCsv", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  downloadCsvContent: state.download,
}));
function wrapper(mocks: MockedResponse[]) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
        <MockedProvider mocks={mocks}>{children}</MockedProvider>
      </NextIntlClientProvider>
    );
  };
}
const roles: MockedResponse = {
  request: { query: LIST_ROLES },
  result: { data: { astroliftRoles: [] } },
};
const page = { items: [], totalCount: 10, nextCursor: null, page: 7, pageSize: 25 };
const csv = {
  filename: "complete.csv",
  content: "Name,Roles\r\nada,operator@APP\r\n",
  rowCount: 5003,
  contentType: "text/csv; charset=utf-8",
};
beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
});

describe("identity whole-list CSV using Apollo", () => {
  it("exports the member filter and stable sort, ignoring the displayed page", async () => {
    state.url = "q=ada&role=org_admin&team=platform&sort=-lastActive,name&page=7";
    const variables = {
      search: "ada",
      filter: { scopeKind: ["ORG"], role: ["org_admin"], team: ["platform"] },
      sort: "-lastActive,name",
    };
    const resultFn = vi.fn(() => ({ data: { astroliftMembersCsv: csv } }));
    const { result } = renderHook(() => useMembers(), {
      wrapper: wrapper([
        roles,
        {
          request: { query: LIST_MEMBERS_PAGE, variables: { ...variables, page: 7, pageSize: 25 } },
          result: { data: { astroliftMembersPage: page } },
        },
        { request: { query: EXPORT_MEMBERS_CSV, variables }, result: resultFn, delay: 20 },
      ]),
    });
    await waitFor(() => expect(result.current.loading).toBe(false));
    await act(async () => {
      await Promise.all([result.current.onExportCsv(), result.current.onExportCsv()]);
    });
    expect(resultFn).toHaveBeenCalledTimes(1);
    expect(state.download).toHaveBeenCalledExactlyOnceWith(csv.filename, csv.content);
    expect(result.current.exportingCsv).toBe(false);
    expect(state.error).not.toHaveBeenCalled();
  });

  it("keeps invitation status, role and truthful activity sort in the export", async () => {
    state.url = "view=invited&status=accepted&role=viewer&sort=-lastActive&page=7";
    const variables = {
      search: null,
      filter: { status: ["accepted"], role: ["viewer"] },
      sort: "-lastActive",
    };
    const { result } = renderHook(() => useMembers(), {
      wrapper: wrapper([
        roles,
        {
          request: {
            query: LIST_INVITATIONS_PAGE,
            variables: { ...variables, page: 7, pageSize: 25 },
          },
          result: { data: { astroliftInvitationsPage: page } },
        },
        {
          request: { query: EXPORT_INVITATIONS_CSV, variables },
          result: { data: { astroliftInvitationsCsv: csv } },
        },
      ]),
    });
    await waitFor(() => expect(result.current.loading).toBe(false));
    await act(() => result.current.onExportCsv());
    expect(state.download).toHaveBeenCalledExactlyOnceWith(csv.filename, csv.content);
    expect(state.error).not.toHaveBeenCalled();
  });

  it("exports matching assignments with the holder filter and no page cap", async () => {
    state.url = "view=mine&role=operator&scope=APP&sort=-created,name&page=7";
    const variables = {
      search: null,
      filter: { holder: ["me"], role: ["operator"], scopeKind: ["APP"] },
      sort: "-created,name",
    };
    const { result } = renderHook(() => useAssignments(), {
      wrapper: wrapper([
        roles,
        {
          request: {
            query: LIST_ROLE_BINDINGS_PAGE,
            variables: { ...variables, page: 7, pageSize: 25 },
          },
          result: { data: { astroliftRoleBindingsPage: page } },
        },
        {
          request: { query: EXPORT_ROLE_BINDINGS_CSV, variables },
          result: { data: { astroliftRoleBindingsCsv: csv } },
        },
      ]),
    });
    await waitFor(() => expect(result.current.page.loading).toBe(false));
    await act(() => result.current.onExportCsv());
    expect(state.download).toHaveBeenCalledExactlyOnceWith(csv.filename, csv.content);
    expect(state.error).not.toHaveBeenCalled();
  });

  it("walks more than 5,000 IdP groups without silently truncating", async () => {
    state.url = "view=groups";
    const count = 5003;
    const groups = Array.from({ length: count }, (_, index) => ({
      name: `group-${index}`,
      kind: "GROUP",
      groupExternalId: `id-${index}`,
      memberCount: 1,
      bindingsCount: 1,
      mappingsCount: 0,
    }));
    const request = (page: number, pageSize: number): MockedResponse => ({
      request: {
        query: PRINCIPAL_SEARCH,
        variables: { search: null, filter: { kind: ["GROUP"] }, page, pageSize },
      },
      result: {
        data: {
          astroliftPrincipalSearch: {
            items: groups.slice((page - 1) * pageSize, page * pageSize),
            totalCount: count,
            nextCursor: null,
          },
        },
      },
      delay: 0,
    });
    const { result } = renderHook(() => useMembers(), {
      wrapper: wrapper([
        roles,
        request(1, 25),
        ...Array.from({ length: 26 }, (_, index) => request(index + 1, 200)),
      ]),
    });
    await waitFor(() => expect(result.current.loading).toBe(false));
    await act(() => result.current.onExportCsv());
    const [filename, content] = state.download.mock.calls[0];
    expect(filename).toBe("people.csv");
    expect(content.trim().split("\r\n")).toHaveLength(count + 1);
    expect(content).toContain("id-5002");
    expect(state.error).not.toHaveBeenCalled();
  });

  it("reports a GraphQL authorization failure, downloads nothing and allows retry", async () => {
    state.url = "";
    const variables = { search: null, filter: { scopeKind: ["ORG"] }, sort: "name" };
    const { result } = renderHook(() => useMembers(), {
      wrapper: wrapper([
        roles,
        {
          request: { query: LIST_MEMBERS_PAGE, variables: { ...variables, page: 1, pageSize: 25 } },
          result: { data: { astroliftMembersPage: page } },
        },
        {
          request: { query: EXPORT_MEMBERS_CSV, variables },
          result: { errors: [new GraphQLError("Export permission revoked")] },
        },
        {
          request: { query: EXPORT_MEMBERS_CSV, variables },
          result: { data: { astroliftMembersCsv: csv } },
        },
      ]),
    });
    await act(() => result.current.onExportCsv());
    expect(state.download).not.toHaveBeenCalled();
    expect(state.error).toHaveBeenCalledWith("Export permission revoked");
    expect(result.current.exportingCsv).toBe(false);
    await act(() => result.current.onExportCsv());
    expect(state.download).toHaveBeenCalledExactlyOnceWith(csv.filename, csv.content);
  });

  it("reports transport failures without creating a partial assignment file", async () => {
    state.url = "";
    const variables = { search: null, filter: null, sort: "-created" };
    const { result } = renderHook(() => useAssignments(), {
      wrapper: wrapper([
        roles,
        {
          request: {
            query: LIST_ROLE_BINDINGS_PAGE,
            variables: { ...variables, page: 1, pageSize: 25 },
          },
          result: { data: { astroliftRoleBindingsPage: page } },
        },
        {
          request: { query: EXPORT_ROLE_BINDINGS_CSV, variables },
          error: new Error("Connection lost"),
        },
      ]),
    });
    await act(() => result.current.onExportCsv());
    expect(state.download).not.toHaveBeenCalled();
    expect(state.error).toHaveBeenCalledWith("Connection lost");
    expect(result.current.exportingCsv).toBe(false);
  });
});
