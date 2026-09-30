import { ApolloClient, ApolloLink, InMemoryCache, Observable } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { AstroliftProject } from "@/graphql/identity/identity.types";
import {
  useNewAgent,
  initialWizardState as agentState,
} from "@/components/screens/agents/new/use-new-agent";
import { useAgentRepoPicker } from "@/components/screens/agents/new/use-agent-repo-picker";
import { useRepoPicker } from "@/components/screens/apps/new/use-repo-picker";
import { initialWizardState as appState } from "@/app/(app)/apps/new/wizard-client";
import { isCiPushableKind } from "@/app/(app)/apps/new/ci-pushable";
import { useAgentProjectStep } from "@/components/screens/agents/new/use-agent-project-step";
import { useAppDetailsStep } from "@/components/screens/apps/new/use-app-details-step";
import { AGENT_REPO_PICKER } from "@/components/screens/agents/new/agents-wizard-b.fixtures";
import { KIND_TO_SOURCE_KIND, usableSourceConnections } from "./source-connection";
import { useWizardProjects } from "./use-wizard-projects";

const context = vi.hoisted(() => ({ orgId: "org-current", push: vi.fn() }));
vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({ org: context.orgId ? { id: context.orgId } : null }),
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: context.push }) }));
vi.mock("sonner", () => ({ toast: { success: vi.fn() } }));
vi.mock("@/components/use-scm-connect", () => ({ useScmConnect: () => ({}) }));

function project(id: string, orgId: string, deletedAt: string | null = null): AstroliftProject {
  return {
    id,
    slug: id,
    name: id,
    organization: { id: orgId, slug: orgId, name: orgId },
    team: { id: "team", slug: "team", name: "team" },
    deletedAt,
    createdAt: "2026-09-01",
    updatedAt: "2026-09-01",
  };
}
const own = project("own-project", "org-current");
const foreign = project("foreign-project", "org-other");
const deleted = project("deleted-project", "org-current", "2026-09-20");

function source(initial: AstroliftProject[] = [own, foreign, deleted]) {
  let rows = initial;
  const requests: string[] = [];
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new ApolloLink(
      (operation) =>
        new Observable((observer) => {
          requests.push(operation.operationName ?? "");
          queueMicrotask(() => {
            const data =
              operation.operationName === "ListProjects"
                ? { astroliftProjects: rows }
                : operation.operationName === "ListTeams"
                  ? { astroliftTeams: [] }
                  : operation.operationName === "ListSourceConnections"
                    ? { astroliftSourceConnections: AGENT_REPO_PICKER.connections }
                    : operation.operationName === "ListAvailableRepos"
                      ? { astroliftAvailableRepos: AGENT_REPO_PICKER.repoList }
                      : operation.operationName === "ClusterCount"
                        ? { astroliftClusterCount: 1 }
                        : operation.operationName === "ListApps"
                          ? { astroliftApps: [] }
                          : operation.operationName === "ListAgentFleet"
                            ? { agentFleet: [] }
                            : operation.operationName === "RegisterAgentRepo"
                              ? {
                                  registerAgentRepo: {
                                    ok: false,
                                    data: null,
                                    errors: [
                                      {
                                        field: "projectId",
                                        message: "Registration denied by the server",
                                        code: "PERMISSION_DENIED",
                                      },
                                    ],
                                  },
                                }
                              : {};
            observer.next({ data });
            observer.complete();
          });
        })
    ),
  });
  return {
    client,
    requests,
    setRows: (next: AstroliftProject[]) => {
      rows = next;
    },
    wrapper: ({ children }: { children: ReactNode }) => (
      <ApolloProvider client={client}>{children}</ApolloProvider>
    ),
  };
}

beforeEach(() => {
  context.orgId = "org-current";
  context.push.mockClear();
});

describe("wizard destinations", () => {
  for (const kind of ["app", "agent"] as const) {
    it(`${kind} requires a currently returned usable connection before reading repositories or advancing`, async () => {
      const server = source();
      const valid = vi.fn();
      const setState = vi.fn();
      const hook = renderHook(
        ({ connectionId }) =>
          kind === "agent"
            ? useAgentRepoPicker({
                state: { ...agentState(), connectionId, sourceRepo: "org/repo" },
                setState,
                setValid: valid,
              })
            : useRepoPicker({
                state: { ...appState(), connectionId, sourceRepo: "org/repo" },
                setState,
                setValid: valid,
                isCiPushableKind,
              }),
        { initialProps: { connectionId: "removed-connection" }, wrapper: server.wrapper }
      );
      await waitFor(() =>
        expect(hook.result.current.connections).toHaveLength(AGENT_REPO_PICKER.connections.length)
      );
      expect(valid).toHaveBeenLastCalledWith(false);
      expect(server.requests).not.toContain("ListAvailableRepos");
      hook.rerender({ connectionId: AGENT_REPO_PICKER.connections[0].id });
      await waitFor(() => expect(valid).toHaveBeenLastCalledWith(true));
      await waitFor(() => expect(server.requests).toContain("ListAvailableRepos"));
      hook.unmount();
      server.client.stop();
    });
  }
  it("does not read projects until the active organization resolves", async () => {
    context.orgId = "";
    const server = source();
    const hook = renderHook(() => useWizardProjects(), { wrapper: server.wrapper });
    expect(server.requests).toEqual([]);
    await expect(hook.result.current.confirmDestination(own.id)).rejects.toThrow(
      /organization is not available/
    );
    expect(server.requests).toEqual([]);
    hook.unmount();
    server.client.stop();
  });

  for (const kind of ["app", "agent"] as const) {
    it(`${kind} accepts only a current live project row, rather than any nonempty ID`, async () => {
      const server = source();
      const setState = vi.fn();
      const valid = vi.fn();
      const hook = renderHook(
        ({ projectId }) =>
          kind === "agent"
            ? useAgentProjectStep({ projectId }, setState, valid)
            : useAppDetailsStep(
                {
                  projectId,
                  name: "Application",
                  slug: "application",
                  slugTouched: true,
                  description: "",
                },
                setState,
                valid
              ),
        { initialProps: { projectId: foreign.id }, wrapper: server.wrapper }
      );
      await waitFor(() => expect(hook.result.current.allProjects).toHaveLength(1));
      expect(valid).toHaveBeenLastCalledWith(false);
      hook.rerender({ projectId: own.id });
      await waitFor(() => expect(valid).toHaveBeenLastCalledWith(true));
      hook.rerender({ projectId: deleted.id });
      expect(valid).toHaveBeenLastCalledWith(false);
      hook.rerender({ projectId: "stale-id" });
      expect(valid).toHaveBeenLastCalledWith(false);
      hook.unmount();
      server.client.stop();
    });
  }

  it("rechecks the destination before register and refuses a revoked or foreign row without writing", async () => {
    const server = source([own]);
    const hook = renderHook(() => useNewAgent(), { wrapper: server.wrapper });
    await waitFor(() => expect(server.requests).toContain("ListProjects"));
    act(() =>
      hook.result.current.setState((state) => ({
        ...state,
        step: 3,
        projectId: own.id,
        sourceRepo: "org/repo",
      }))
    );
    await waitFor(() => expect(hook.result.current.projectLabel).toBe("team/own-project"));
    server.setRows([{ ...own, organization: foreign.organization }]);
    act(() => hook.result.current.onContinue());
    await waitFor(() =>
      expect(hook.result.current.submitError).toMatch(/no longer available in this organization/)
    );
    expect(server.requests.filter((name) => name === "ListProjects")).toHaveLength(2);
    expect(server.requests).not.toContain("RegisterAgentRepo");
    expect(context.push).not.toHaveBeenCalled();
    hook.unmount();
    server.client.stop();
  });

  it("retains the actual server denial after a valid destination read", async () => {
    const server = source([own]);
    const hook = renderHook(() => useNewAgent(), { wrapper: server.wrapper });
    act(() =>
      hook.result.current.setState((state) => ({
        ...state,
        step: 3,
        projectId: own.id,
        sourceRepo: "org/repo",
      }))
    );
    await waitFor(() => expect(hook.result.current.projectLabel).toBe("team/own-project"));
    act(() => hook.result.current.onContinue());
    await waitFor(() =>
      expect(hook.result.current.submitError).toBe("Registration denied by the server")
    );
    expect(server.requests).toContain("RegisterAgentRepo");
    expect(context.push).not.toHaveBeenCalled();
    hook.unmount();
    server.client.stop();
  });

  it("shares host mappings while retaining personal connections and excluding unusable config rows", () => {
    const connection = AGENT_REPO_PICKER.connections[0];
    expect(
      usableSourceConnections([
        { ...connection, isPersonal: true },
        { ...connection, isActive: false },
        { ...connection, isOauthAppConfig: true },
      ])
    ).toEqual([{ ...connection, isPersonal: true }]);
    expect(KIND_TO_SOURCE_KIND.github_app_install).toBe("github");
    expect(KIND_TO_SOURCE_KIND.gitlab_oauth_user).toBe("gitlab");
    expect(KIND_TO_SOURCE_KIND.gitea_pat).toBe("gitea");
    expect(KIND_TO_SOURCE_KIND.bitbucket_pat).toBe("bitbucket");
  });
});
