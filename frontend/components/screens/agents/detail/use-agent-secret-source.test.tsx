import { ApolloClient, ApolloLink, InMemoryCache, Observable } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, render, renderHook, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { PermissionsProvider } from "@/providers/PermissionsProvider";
import messages from "@/messages/en.json";
import { AgentSecretSource, type SecretSourceOption } from "./AgentSecretSource";
import { AgentSecretValues } from "./AgentSecretsTab";
import { useAgentSecretValues } from "./use-agent-secrets-tab";
import { useAgentSecretSource } from "./use-agent-secret-source";

const active = vi.hoisted(() => ({ orgId: "org-one" }));
vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({ org: active.orgId ? { id: active.orgId } : null }),
}));
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/agents/agent-one/secrets",
  useRouter: () => ({ replace: vi.fn() }),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const sameSlug = {
  id: "unrelated-same-slug",
  slug: "agent-one",
  name: "Unrelated Same Slug",
  teamId: null,
  projectId: null,
};
const chosen = {
  id: "chosen-spec",
  slug: "shared-recipe",
  name: "Shared Recipe",
  teamId: "team-one",
  projectId: null,
};
const second = {
  id: "second-spec",
  slug: "second-recipe",
  name: "Second Recipe",
  teamId: null,
  projectId: "project-one",
};
const grants = ["agent_env_spec.read", "secret.list", "secret.read", "secret.update"];

function server(permissions = grants, permissionLoading = false) {
  const requests: Array<{ name: string; variables: Record<string, unknown>; org: unknown }> = [];
  let recipe: SecretSourceOption | null = chosen;
  let failDetail = false;
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new ApolloLink(
      (operation) =>
        new Observable((observer) => {
          requests.push({
            name: operation.operationName ?? "",
            variables: operation.variables,
            org: operation.getContext().headers?.["X-Astrolift-Organization"],
          });
          queueMicrotask(() => {
            if (operation.operationName === "AgentSecretSourceDetail" && failDetail) {
              observer.error(new Error("verification unavailable"));
              return;
            }
            const data =
              operation.operationName === "AgentSecretSourceOptions"
                ? {
                    agentEnvironmentSpecsPage: {
                      items:
                        operation.variables.page === 2
                          ? [second]
                          : operation.variables.search
                            ? [chosen]
                            : [sameSlug, chosen],
                      totalCount: operation.variables.search ? 1 : 21,
                      page: operation.variables.page,
                      pageSize: 20,
                    },
                  }
                : operation.operationName === "AgentSecretSourceDetail"
                  ? { agentEnvironmentSpec: recipe }
                  : operation.operationName === "AgentSecretStatusPage"
                    ? {
                        agentEnvironmentSpecSecretStatusPage: {
                          items: [
                            {
                              envVar: "TOKEN",
                              uri: "aws-sm://agents/org-one/token",
                              exists: true,
                              error: null,
                              provider: "aws-sm",
                              canReveal: true,
                              readLimitation: null,
                            },
                          ],
                          totalCount: 1,
                          page: 1,
                          pageSize: 25,
                          error: null,
                        },
                      }
                    : operation.operationName === "RevealAgentSecretValue"
                      ? {
                          revealAgentSecretValue: {
                            ok: true,
                            errors: [],
                            data: {
                              envVar: "TOKEN",
                              uri: "aws-sm://agents/org-one/token",
                              value: "revealed-only-for-chosen-recipe",
                              provider: "aws-sm",
                            },
                          },
                        }
                      : operation.operationName === "UpsertAgentSecretRef"
                        ? {
                            upsertAgentSecretRef: {
                              ok: true,
                              errors: [],
                              data: {
                                envVar: "TOKEN",
                                uri: "aws-sm://agents/org-one/token",
                                exists: true,
                              },
                            },
                          }
                        : {};
            observer.next({ data });
            observer.complete();
          });
        })
    ),
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <ApolloProvider client={client}>
      <PermissionsProvider value={{ granted: new Set(permissions), loading: permissionLoading }}>
        <NextIntlClientProvider locale="en" messages={messages}>
          {children}
        </NextIntlClientProvider>
      </PermissionsProvider>
    </ApolloProvider>
  );
  return {
    client,
    requests,
    wrapper,
    recipe: (value: SecretSourceOption | null) => {
      recipe = value;
    },
    fail: () => {
      failDetail = true;
    },
  };
}

beforeEach(() => {
  active.orgId = "org-one";
});

describe("explicit environment-recipe source", () => {
  it("does not infer a same-slug recipe and searches/pages only bounded server results", async () => {
    const api = server();
    const hook = renderHook(() => useAgentSecretSource("agent-one"), { wrapper: api.wrapper });
    await waitFor(() => expect(hook.result.current.source.options).toHaveLength(2));
    expect(hook.result.current.confirmedSpec).toBeNull();
    expect(hook.result.current.source.selection).toBeNull();
    expect(api.requests.map((request) => request.name)).toEqual(["AgentSecretSourceOptions"]);
    act(() => hook.result.current.source.onPage(2));
    await waitFor(() => expect(hook.result.current.source.options[0]?.id).toBe(second.id));
    expect(api.requests[1]).toMatchObject({
      variables: { orgId: "org-one", page: 2, pageSize: 20 },
      org: "org-one",
    });
    act(() => hook.result.current.source.onSearch("shared"));
    await waitFor(() =>
      expect(
        api.requests.some(
          (request) => request.variables.search === "shared" && request.variables.page === 1
        )
      ).toBe(true)
    );
    act(() => hook.result.current.source.onSelect("not-returned"));
    expect(hook.result.current.source.selection).toBeNull();
    expect(api.requests.every((request) => request.name === "AgentSecretSourceOptions")).toBe(true);
    hook.unmount();
    api.client.stop();
  });

  for (const scenario of ["missing-org", "missing-permission", "unknown-permission"] as const) {
    it(`skips source reads for ${scenario}`, () => {
      if (scenario === "missing-org") active.orgId = "";
      const api = server(
        scenario === "missing-permission" ? [] : grants,
        scenario === "unknown-permission"
      );
      const hook = renderHook(() => useAgentSecretSource("agent-one"), { wrapper: api.wrapper });
      expect(api.requests).toEqual([]);
      expect(hook.result.current.source.access).toBe(
        scenario === "missing-permission" ? "denied" : "loading"
      );
      expect(hook.result.current.confirmedSpec).toBeNull();
      hook.unmount();
      api.client.stop();
    });
  }

  it("keeps secret-list and bundle-read permissions independent of spec visibility", async () => {
    const api = server(["agent_env_spec.read", "secret.list"]);
    const hook = renderHook(() => useAgentSecretSource("agent-one"), { wrapper: api.wrapper });
    await waitFor(() => expect(hook.result.current.source.optionsLoading).toBe(false));
    act(() => hook.result.current.source.onSelect(chosen.id));
    await waitFor(() => expect(hook.result.current.confirmedSpec?.id).toBe(chosen.id));
    expect(hook.result.current.source.canListSecrets).toBe(true);
    expect(hook.result.current.canReadSecretBundles).toBe(false);
    expect(api.requests.every((request) => request.name.startsWith("AgentSecretSource"))).toBe(
      true
    );
    hook.unmount();
    api.client.stop();
  });

  it("requires the chosen ID and current visibility, including revocation and a reused slug", async () => {
    const api = server();
    const hook = renderHook(({ slug }) => useAgentSecretSource(slug), {
      initialProps: { slug: "agent-one" },
      wrapper: api.wrapper,
    });
    await waitFor(() => expect(hook.result.current.source.optionsLoading).toBe(false));
    act(() => hook.result.current.source.onSelect(chosen.id));
    await waitFor(() => expect(hook.result.current.confirmedSpec?.id).toBe(chosen.id));
    expect(
      api.requests.find((request) => request.name === "AgentSecretSourceDetail")
    ).toMatchObject({ variables: { slug: chosen.slug, orgId: "org-one" }, org: "org-one" });
    api.recipe({ ...chosen, id: "reused-slug-new-id" });
    act(() => hook.result.current.source.onVerify());
    await waitFor(() => expect(hook.result.current.source.sourceState).toBe("unavailable"));
    expect(hook.result.current.confirmedSpec).toBeNull();
    api.recipe(null);
    act(() => hook.result.current.source.onVerify());
    await waitFor(() => expect(hook.result.current.source.sourceState).toBe("unavailable"));
    hook.rerender({ slug: "agent-two" });
    expect(hook.result.current.source.selection).toBeNull();
    active.orgId = "org-two";
    hook.rerender({ slug: "agent-one" });
    expect(hook.result.current.source.selection).toBeNull();
    expect(hook.result.current.confirmedSpec).toBeNull();
    hook.unmount();
    api.client.stop();
  });

  it("shows a verification failure without retaining the confirmed recipe", async () => {
    const api = server();
    const hook = renderHook(() => useAgentSecretSource("agent-one"), { wrapper: api.wrapper });
    await waitFor(() => expect(hook.result.current.source.optionsLoading).toBe(false));
    act(() => hook.result.current.source.onSelect(chosen.id));
    await waitFor(() => expect(hook.result.current.confirmedSpec?.id).toBe(chosen.id));
    api.fail();
    act(() => hook.result.current.source.onVerify());
    await waitFor(() => expect(hook.result.current.source.sourceState).toBe("error"));
    expect(hook.result.current.confirmedSpec).toBeNull();
    hook.unmount();
    api.client.stop();
  });

  it("targets confirmed recipe mutations and drops revealed values when the source or agent changes", async () => {
    const api = server();
    let source!: ReturnType<typeof useAgentSecretSource>;
    let values!: ReturnType<typeof useAgentSecretValues>;
    function Values({ slug }: { slug: string }) {
      values = useAgentSecretValues(slug);
      return <AgentSecretValues {...values} />;
    }
    function Probe({ slug }: { slug: string }) {
      source = useAgentSecretSource(slug);
      return (
        <AgentSecretSource {...source.source}>
          {source.confirmedSpec && source.source.canListSecrets && (
            <Values key={source.editorKey} slug={source.confirmedSpec.slug} />
          )}
        </AgentSecretSource>
      );
    }
    const view = render(<Probe slug="agent-one" />, { wrapper: api.wrapper });
    await waitFor(() => expect(source!.source.optionsLoading).toBe(false));
    expect(api.requests.some((request) => /SecretStatus/.test(request.name))).toBe(false);
    act(() => source.source.onSelect(chosen.id));
    await waitFor(() => expect(values!.rows).toHaveLength(1));
    expect(
      api.requests.find((request) => /SecretStatusPage/.test(request.name))?.variables.slug
    ).toBe(chosen.slug);
    await act(async () => {
      await values.onReveal(values.rows[0]);
    });
    expect(await screen.findByText("revealed-only-for-chosen-recipe")).toBeInTheDocument();
    expect(
      api.requests.find((request) => request.name === "RevealAgentSecretValue")?.variables.slug
    ).toBe(chosen.slug);
    await act(async () => {
      await values.onUpsertRef("TOKEN", "aws-sm://agents/org-one/token");
    });
    expect(
      api.requests.find((request) => request.name === "UpsertAgentSecretRef")?.variables.slug
    ).toBe(chosen.slug);
    view.rerender(<Probe slug="agent-two" />);
    expect(source.source.selection).toBeNull();
    expect(screen.queryByText("revealed-only-for-chosen-recipe")).not.toBeInTheDocument();
    await waitFor(() => expect(source.source.optionsLoading).toBe(false));
    act(() => source.source.onSelect(chosen.id));
    await waitFor(() => expect(values.rows).toHaveLength(1));
    await act(async () => {
      await values.onReveal(values.rows[0]);
    });
    expect(await screen.findByText("revealed-only-for-chosen-recipe")).toBeInTheDocument();
    api.recipe(null);
    act(() => source.source.onVerify());
    await waitFor(() => expect(source.source.sourceState).toBe("unavailable"));
    expect(screen.queryByText("revealed-only-for-chosen-recipe")).not.toBeInTheDocument();
    active.orgId = "org-two";
    view.rerender(<Probe slug="agent-two" />);
    expect(source.source.selection).toBeNull();
    expect(screen.queryByRole("button", { name: "Add binding" })).not.toBeInTheDocument();
    view.unmount();
    api.client.stop();
  });
});
