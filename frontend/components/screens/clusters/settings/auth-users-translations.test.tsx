import { readFileSync } from "node:fs";
import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import {
  parse as parseMessage,
  type MessageFormatElement,
} from "@formatjs/icu-messageformat-parser";
import { act, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { buildSchema, parse, validate } from "graphql";
import { createTranslator, NextIntlClientProvider } from "next-intl";
import { hydrateRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ClusterSettingsClient } from "@/app/(app)/clusters/[slug]/settings/cluster-settings-client";
import { locales } from "@/i18n/config";
import { AuthUsersView } from "./AuthUsers";
import { useAuthUsers } from "./use-auth-users";
import { AUTH_USERS, CLUSTER } from "./fixtures";
import { localizedAuthUsersList, AUTH_USERS_LIST } from "./auth-users-list";

const state = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  allowed: true,
}));
vi.mock("sonner", () => ({ toast: state }));
vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({
    granted: new Set(state.allowed ? ["cluster.users"] : []),
    loading: false,
    can: () => state.allowed,
  }),
}));
vi.mock("next/navigation", () => ({
  usePathname: () => "/clusters/prod-west/settings",
  useSearchParams: () => new URLSearchParams("section=users"),
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));
const schema = buildSchema(readFileSync("../backend/schema.graphql", "utf8"));
const catalogs = Object.fromEntries(
  locales.map((locale) => [locale, JSON.parse(readFileSync(`messages/${locale}.json`, "utf8"))])
);
const tFor = (locale: string, namespace = "clusterSettings.authUsers") =>
  createTranslator({ locale, messages: catalogs[locale], namespace });
const user = {
  __typename: "AstroliftClusterAuthUser",
  username: "USERNAME_LITERAL",
  providerUserId: "SUBJECT_LITERAL",
  email: "literal-user@example.test",
  enabled: true,
  status: "CONFIRMED",
  createdAt: "2026-09-20T12:00:00Z",
  groups: ["GROUP_A_LITERAL"],
};
const observed = {
  __typename: "AstroliftClusterAuthUsers",
  supported: true,
  reason: "",
  provider: "Amazon Cognito",
  source: {
    __typename: "AstroliftClusterAuthSource",
    providerPluginId: "00000000-0000-0000-0000-000000000001",
    providerPoolId: "us-west-2_POOL_LITERAL",
    sourceVersion: "SOURCE_VERSION_LITERAL",
  },
  reachNote:
    "A user of this pool can sign in to every app on the cluster that has no access rule of its own.",
  users: [user],
  groups: ["GROUP_A_LITERAL", "GROUP_B_LITERAL"],
};
const cluster = {
  ...CLUSTER,
  __typename: "AstroliftTenantCluster",
  oidcAuthConfig: null,
  organizationSlug: "ORG_LITERAL",
  isActive: true,
  managedAt: null,
  lastHeartbeatAt: null,
  heartbeatStatus: "UNKNOWN",
  heartbeatAgeSeconds: null,
  createdByUsername: "ACTOR_LITERAL",
  lastBootstrapRun: null,
};
type Action = "create" | "password" | "reset" | "enabled" | "delete" | "groups" | "group";
const actions: Action[] = ["create", "password", "reset", "enabled", "delete", "groups", "group"];
const names = {
  create: "CreateClusterAuthUser",
  password: "SetClusterAuthUserPassword",
  reset: "ResetClusterAuthUserPassword",
  enabled: "SetClusterAuthUserEnabled",
  delete: "DeleteClusterAuthUser",
  groups: "SetClusterAuthUserGroups",
  group: "CreateClusterAuthGroup",
};
const fields = {
  create: "createClusterAuthUser",
  password: "setClusterAuthUserPassword",
  reset: "resetClusterAuthUserPassword",
  enabled: "setClusterAuthUserEnabled",
  delete: "deleteClusterAuthUser",
  groups: "setClusterAuthUserGroups",
  group: "createClusterAuthGroup",
};
const success = {
  create: "createdPassword",
  password: "passwordAccepted",
  reset: "resetAccepted",
  enabled: "disabledAccepted",
  delete: "deleteAccepted",
  groups: "groupsAccepted",
  group: "groupCreated",
};
const failed = {
  create: "createFailed",
  password: "passwordFailed",
  reset: "resetFailed",
  enabled: "enabledFailed",
  delete: "deleteFailed",
  groups: "groupsFailed",
  group: "createGroupFailed",
};
const refreshes: Action[] = ["create", "enabled", "delete", "groups", "group"];
type Mode =
  | "ok"
  | "refused"
  | "transport"
  | "no-message"
  | "refresh-failed"
  | "refused-read-failed"
  | "read-failed"
  | "read-null"
  | "unsupported"
  | "withdrawn"
  | "replaced-user"
  | "deferred"
  | "unknown-source"
  | "unknown-user"
  | "replaced-subject"
  | "source-version";
type Request = {
  operationName: string;
  variables: { input?: Record<string, unknown>; clusterId?: string; slug?: string };
};
function context(locale: string, initialMode: Mode = "ok", selectedAction: Action = "create") {
  const source = structuredClone(observed);
  let mode = initialMode,
    accepted = false,
    release: (() => void) | undefined;
  const requests: Request[] = [];
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new HttpLink({
      uri: "https://auth-users.test.invalid/graphql/",
      fetch: async (_url, options) => {
        const request = JSON.parse(String(options?.body));
        requests.push(request);
        expect(validate(schema, parse(request.query))).toEqual([]);
        let data;
        if (request.operationName === "GetCluster")
          data = {
            astroliftCluster: {
              ...cluster,
              id: request.variables.slug === "next" ? "NEXT_CLUSTER_ID" : cluster.id,
              slug: request.variables.slug === "next" ? "next" : cluster.slug,
            },
          };
        else if (request.operationName === "ClusterAuthUsers") {
          if (mode === "read-failed" || (accepted && mode === "refresh-failed"))
            throw new Error("RAW_AUTH_SOURCE_READ_ERROR");
          const next = request.variables.clusterId === "NEXT_CLUSTER_ID";
          data = {
            astroliftClusterAuthUsers:
              mode === "read-null"
                ? null
                : mode === "unsupported"
                  ? {
                      ...observed,
                      supported: false,
                      reason: "RAW_PROVIDER_UNAVAILABLE_REASON",
                      users: [],
                    }
                  : {
                      ...source,
                      source:
                        mode === "unknown-source"
                          ? null
                          : mode === "source-version"
                            ? { ...source.source, sourceVersion: "NEW_SOURCE_VERSION" }
                            : source.source,
                      users:
                        mode === "withdrawn"
                          ? []
                          : next
                            ? [
                                {
                                  ...user,
                                  username: "NEXT_USERNAME_LITERAL",
                                  email: "next-user@example.test",
                                },
                              ]
                            : mode === "unknown-user"
                              ? [{ ...user, providerUserId: null }]
                              : mode === "replaced-subject"
                                ? [{ ...user, providerUserId: "REPLACEMENT_SUBJECT_LITERAL" }]
                                : mode === "replaced-user"
                                  ? [{ ...user, createdAt: "2026-10-01T12:00:00Z" }]
                                  : source.users,
                    },
          };
        } else {
          const action = actions.find((action) => names[action] === request.operationName);
          if (!action) throw new Error("Unexpected operation " + request.operationName);
          if (action === selectedAction && mode === "deferred")
            await new Promise<void>((resolve) => {
              release = resolve;
            });
          if (action === selectedAction && mode === "transport")
            throw new Error("RAW_PROVIDER_TRANSPORT_ERROR");
          const ok =
            action !== selectedAction ||
            !["refused", "refused-read-failed", "no-message"].includes(mode);
          if (ok) {
            accepted = true;
            const input = request.variables.input;
            if (action === "create")
              source.users.push({
                ...user,
                username: "CREATED_USERNAME_LITERAL",
                email: input.email,
                groups: input.groups,
              });
            if (action === "enabled") source.users[0].enabled = input.enabled;
            if (action === "delete") source.users = [];
            if (action === "groups")
              source.users[0].groups = [
                ...source.users[0].groups.filter((group) => !input.remove.includes(group)),
                ...input.add,
              ];
            if (action === "group") source.groups.push(input.name);
          }
          data = {
            [fields[action]]: {
              ok,
              errors:
                ok || mode === "no-message"
                  ? []
                  : [
                      {
                        code: "PERMISSION_DENIED",
                        message: "RAW_PROVIDER_REFUSAL",
                        field: "username",
                      },
                    ],
              ...(action === "create"
                ? {
                    data: ok
                      ? {
                          __typename: "AstroliftClusterAuthUser",
                          username: "CREATED_USERNAME_LITERAL",
                          email: request.variables.input.email,
                        }
                      : null,
                  }
                : {}),
            },
          };
        }
        return Response.json({ data });
      },
    }),
  });
  function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <NextIntlClientProvider
        locale={locale}
        messages={catalogs[locale]}
        now={new Date("2026-10-01T12:00:00Z")}
        timeZone="America/Costa_Rica"
      >
        <ApolloProvider client={client}>{children}</ApolloProvider>
      </NextIntlClientProvider>
    );
  }
  return {
    client,
    requests,
    Wrapper,
    setMode: (next: Mode) => {
      mode = next;
    },
    waiting: () => !!release,
    release: () => release?.(),
  };
}
function mount(locale: string, mode: Mode = "ok", action: Action = "create") {
  const ctx = context(locale, mode, action);
  const result = render(<ClusterSettingsClient slug={cluster.slug} />, { wrapper: ctx.Wrapper });
  return { ...ctx, result };
}
async function rowMenu(locale: string) {
  const label = tFor(locale, "shared.list")("rowActions", { label: tFor(locale)("title") });
  await userEvent.click(await screen.findByRole("button", { name: label }));
}
async function begin(locale: string, action: Action) {
  const t = tFor(locale);
  if (action === "create") {
    await userEvent.click(await screen.findByRole("button", { name: t("addUser") }));
    await userEvent.type(screen.getByLabelText(t("email")), "new-user@example.test");
    await userEvent.type(screen.getByLabelText(t("optionalPassword")), "TEST_ONLY_CREATE_PASSWORD");
    await userEvent.click(screen.getByLabelText(t("createPermanent")));
    await userEvent.click(screen.getByLabelText("GROUP_A_LITERAL"));
    return () =>
      userEvent.click(
        within(screen.getByRole("dialog")).getByRole("button", { name: t("addUser") })
      );
  }
  if (action === "groups" || action === "group") {
    const input = await screen.findByLabelText(t("addToGroup", { name: user.email }));
    await userEvent.type(input, action === "group" ? "NEW_GROUP_LITERAL" : "GROUP_B_LITERAL");
    return async () => {
      input.focus();
      await userEvent.keyboard("{Enter}");
    };
  }
  await rowMenu(locale);
  if (action === "enabled")
    return () => userEvent.click(screen.getByRole("menuitem", { name: t("disable") }));
  if (action === "delete") {
    await userEvent.click(
      screen.getByRole("menuitem", { name: t("deleteName", { name: user.email }) })
    );
    return () => userEvent.click(screen.getByRole("button", { name: t("deleteUser") }));
  }
  await userEvent.click(screen.getByRole("menuitem", { name: t("password") }));
  if (action === "reset")
    return () => userEvent.click(screen.getByRole("button", { name: t("requestReset") }));
  await userEvent.type(screen.getByLabelText(t("newPassword")), "TEST_ONLY_NEW_PASSWORD");
  return () => userEvent.click(screen.getByRole("button", { name: t("setPassword") }));
}
function expected(action: Action) {
  const { __typename: _, ...expectedSource } = observed.source;
  const target = {
    clusterId: cluster.id,
    expectedSource,
    expectedUserId: user.providerUserId,
    username: user.username,
  };
  switch (action) {
    case "create":
      return {
        clusterId: cluster.id,
        expectedSource,
        email: "new-user@example.test",
        password: "TEST_ONLY_CREATE_PASSWORD",
        permanent: true,
        groups: ["GROUP_A_LITERAL"],
      };
    case "password":
      return { ...target, password: "TEST_ONLY_NEW_PASSWORD", permanent: true };
    case "reset":
    case "delete":
      return target;
    case "enabled":
      return { ...target, enabled: false };
    case "groups":
      return { ...target, add: ["GROUP_B_LITERAL"], remove: [] };
    case "group":
      return { clusterId: cluster.id, expectedSource, name: "NEW_GROUP_LITERAL" };
  }
}
function writes(requests: Request[]) {
  return requests.filter((r) => Object.values(names).includes(r.operationName));
}
beforeEach(() => {
  vi.clearAllMocks();
  state.allowed = true;
  localStorage.clear();
});

describe.each(locales)("Connected auth-user operations in %s", (locale) => {
  it("keeps unknown source proof read-only without claiming a read error", async () => {
    const ctx = mount(locale, "unknown-source"),
      t = tFor(locale);
    await screen.findByText(user.email);
    expect(screen.getByRole("status")).toHaveTextContent(t("unknownSource"));
    expect(screen.queryByRole("button", { name: t("addUser") })).toBeNull();
    expect(screen.getByLabelText(t("addToGroup", { name: user.email }))).toBeDisabled();
    expect(writes(ctx.requests)).toHaveLength(0);
  });
  it("keeps a user with unknown immutable subject read-only", async () => {
    const ctx = mount(locale, "unknown-user"),
      t = tFor(locale);
    await screen.findByText(user.email);
    expect(screen.getByLabelText(t("addToGroup", { name: user.email }))).toBeDisabled();
    await rowMenu(locale);
    expect(screen.getByRole("menuitem", { name: t("password") })).toHaveAttribute(
      "aria-disabled",
      "true"
    );
    expect(writes(ctx.requests)).toHaveLength(0);
  });
  it.each(actions)(
    "%s uses exact literal input and honest provider-accepted feedback",
    async (action) => {
      const ctx = mount(locale, "ok", action),
        t = tFor(locale);
      const submit = await begin(locale, action);
      await submit();
      await waitFor(() => expect(state.success).toHaveBeenCalledWith(t(success[action])));
      expect(
        writes(ctx.requests).find((r) => r.operationName === names[action])?.variables
      ).toEqual({ input: expected(action) });
      const reads = ctx.requests.filter((r) => r.operationName === "ClusterAuthUsers");
      if (refreshes.includes(action))
        await waitFor(() =>
          expect(ctx.requests.filter((r) => r.operationName === "ClusterAuthUsers")).toHaveLength(
            action === "group" ? 3 : 2
          )
        );
      else expect(reads).toHaveLength(1);
      expect(
        ctx.requests
          .filter((r) => r.operationName === "ClusterAuthUsers")
          .every((r) => JSON.stringify(r.variables) === JSON.stringify({ clusterId: cluster.id }))
      ).toBe(true);
      expect(state.error).not.toHaveBeenCalled();
      if (["create", "password", "reset", "delete"].includes(action))
        await waitFor(() =>
          expect(screen.queryByRole(action === "delete" ? "alertdialog" : "dialog")).toBeNull()
        );
    }
  );
  it.each(
    actions.flatMap((action) =>
      (["refused", "transport", "no-message", "refused-read-failed"] as const).map(
        (mode) => [action, mode] as const
      )
    )
  )("%s/%s preserves refusal and does no refresh", async (action, mode) => {
    const ctx = mount(locale, mode, action),
      t = tFor(locale);
    const submit = await begin(locale, action);
    await submit();
    await waitFor(() =>
      expect(state.error).toHaveBeenCalledWith(
        mode === "no-message"
          ? t(failed[action])
          : mode === "transport"
            ? "RAW_PROVIDER_TRANSPORT_ERROR"
            : "RAW_PROVIDER_REFUSAL"
      )
    );
    expect(state.success).not.toHaveBeenCalled();
    expect(state.warning).not.toHaveBeenCalled();
    expect(ctx.requests.filter((r) => r.operationName === "ClusterAuthUsers")).toHaveLength(1);
    expect(writes(ctx.requests)).toHaveLength(1);
    if (action === "create")
      expect(screen.getByLabelText(t("optionalPassword"))).toHaveValue("TEST_ONLY_CREATE_PASSWORD");
    if (action === "password")
      expect(screen.getByLabelText(t("newPassword"))).toHaveValue("TEST_ONLY_NEW_PASSWORD");
    if (action === "groups" || action === "group")
      expect(screen.getByLabelText(t("addToGroup", { name: user.email }))).toHaveValue(
        action === "groups" ? "GROUP_B_LITERAL" : "NEW_GROUP_LITERAL"
      );
    if (["create", "password", "reset"].includes(action))
      expect(screen.getByRole("dialog")).toBeTruthy();
    if (action === "delete") expect(screen.getByRole("alertdialog")).toBeTruthy();
  });
  it.each(refreshes)("%s accepted write survives a failed owned read", async (action) => {
    const ctx = mount(locale, "refresh-failed", action),
      t = tFor(locale);
    const submit = await begin(locale, action);
    await submit();
    await waitFor(() => expect(state.success).toHaveBeenCalledWith(t(success[action])));
    await waitFor(() => expect(state.warning).toHaveBeenCalledWith(t("refreshWarning")));
    expect(state.error).not.toHaveBeenCalled();
    expect(await screen.findByText("RAW_AUTH_SOURCE_READ_ERROR")).toBeTruthy();
    if (action === "group")
      await waitFor(() => expect(state.success).toHaveBeenCalledWith(t("groupsAccepted")));
    ctx.setMode("ok");
    await userEvent.click(screen.getByRole("button", { name: t("retry") }));
    await waitFor(() => expect(screen.queryByText("RAW_AUTH_SOURCE_READ_ERROR")).toBeNull());
    expect(ctx.requests.filter((r) => r.operationName === names[action])).toHaveLength(1);
  });
  it("a failed initial source offers real retry without inventing empty users or admission", async () => {
    const ctx = mount(locale, "read-failed"),
      t = tFor(locale);
    expect(await screen.findByText("RAW_AUTH_SOURCE_READ_ERROR")).toBeTruthy();
    expect(screen.queryByRole("button", { name: t("addUser") })).toBeNull();
    expect(screen.queryByText(t("noUsers"))).toBeNull();
    ctx.setMode("ok");
    await userEvent.click(screen.getByRole("button", { name: t("retry") }));
    expect(await screen.findByText(user.email)).toBeTruthy();
    expect(writes(ctx.requests)).toHaveLength(0);
  });
  it.each(["read-null", "unsupported"] as const)(
    "%s source has no user writes and preserves the provider reason",
    async (mode) => {
      const ctx = mount(locale, mode),
        t = tFor(locale);
      expect(
        await screen.findByText(t(mode === "read-null" ? "unknownSource" : "unsupported"))
      ).toBeTruthy();
      expect(screen.queryByRole("button", { name: t("addUser") })).toBeNull();
      if (mode === "unsupported")
        expect(screen.getByText("RAW_PROVIDER_UNAVAILABLE_REASON")).toBeTruthy();
      expect(writes(ctx.requests)).toHaveLength(0);
    }
  );
  it("the actual parent permission fence does not mount an unauthorized provider read", async () => {
    state.allowed = false;
    const ctx = mount(locale);
    await screen.findByText(cluster.slug);
    await waitFor(() => expect(ctx.requests).toHaveLength(1));
    expect(ctx.requests[0].operationName).toBe("GetCluster");
    expect(writes(ctx.requests)).toHaveLength(0);
  });
  it("a cached same-target failure retains a typed password but confirmed source withdrawal clears it", async () => {
    const ctx = mount(locale),
      t = tFor(locale);
    await begin(locale, "password");
    ctx.setMode("read-failed");
    await act(async () => {
      await ctx.client.refetchQueries({ include: ["ClusterAuthUsers"] }).catch(() => {});
    });
    expect(screen.getByLabelText(t("newPassword"))).toHaveValue("TEST_ONLY_NEW_PASSWORD");
    ctx.setMode("withdrawn");
    await act(async () => {
      await ctx.client.refetchQueries({ include: ["ClusterAuthUsers"] });
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(writes(ctx.requests)).toHaveLength(0);
  });
});

describe.each(locales)("Observed source leases in %s", (locale) => {
  it.each(["replaced-user", "replaced-subject", "source-version"] as const)(
    "rejects old callbacks after %s and ABA without provider writes",
    async (changed) => {
      const ctx = context(locale),
        t = tFor(locale);
      const hook = renderHook(({ id }) => useAuthUsers(id), {
        initialProps: { id: cluster.id },
        wrapper: ctx.Wrapper,
      });
      await waitFor(() => expect(hook.result.current.view?.supported).toBe(true));
      const stale = hook.result.current.onSetPassword;
      ctx.setMode(changed);
      await act(async () => {
        await ctx.client.refetchQueries({ include: ["ClusterAuthUsers"] });
      });
      await act(async () =>
        expect(await stale(user.username, "TEST_ONLY_STALE_PASSWORD", true)).toBe(false)
      );
      ctx.setMode("ok");
      await act(async () => {
        await ctx.client.refetchQueries({ include: ["ClusterAuthUsers"] });
      });
      await act(async () =>
        expect(await stale(user.username, "TEST_ONLY_STALE_PASSWORD", true)).toBe(false)
      );
      expect(writes(ctx.requests)).toHaveLength(0);
      expect(state.error).toHaveBeenCalledWith(t("sourceChanged"));
      hook.rerender({ id: "NEXT_CLUSTER_ID" });
      await waitFor(() =>
        expect(hook.result.current.view?.users[0]?.username).toBe("NEXT_USERNAME_LITERAL")
      );
      await act(async () =>
        expect(await stale(user.username, "TEST_ONLY_STALE_PASSWORD", true)).toBe(false)
      );
      expect(writes(ctx.requests)).toHaveLength(0);
    }
  );
  it("keeps a newer cluster password review when a previous accepted request completes", async () => {
    const ctx = mount(locale, "deferred", "password"),
      t = tFor(locale);
    const submit = await begin(locale, "password");
    await submit();
    await waitFor(() => expect(ctx.waiting()).toBe(true));
    ctx.result.rerender(<ClusterSettingsClient slug="next" />);
    await screen.findByText("next-user@example.test");
    await rowMenu(locale);
    await userEvent.click(screen.getByRole("menuitem", { name: t("password") }));
    const password = screen.getByLabelText(t("newPassword"));
    expect(password).toHaveValue("");
    await userEvent.type(password, "TEST_ONLY_NEXT_PRIVATE_DRAFT");
    await act(async () => ctx.release());
    await waitFor(() => expect(state.success).toHaveBeenCalledWith(t("passwordAccepted")));
    expect(screen.getByLabelText(t("newPassword"))).toHaveValue("TEST_ONLY_NEXT_PRIVATE_DRAFT");
    expect(writes(ctx.requests)).toHaveLength(1);
    expect(writes(ctx.requests)[0].variables).toEqual({ input: expected("password") });
  });
  it("reports an accepted group creation separately from a refused membership write", async () => {
    const ctx = mount(locale, "refused", "groups"),
      t = tFor(locale);
    await screen.findByText(user.email);
    const input = screen.getByLabelText(t("addToGroup", { name: user.email }));
    await userEvent.type(input, "NEW_GROUP_LITERAL");
    input.focus();
    await userEvent.keyboard("{Enter}");
    await waitFor(() => expect(state.success).toHaveBeenCalledWith(t("groupCreated")));
    await waitFor(() => expect(state.error).toHaveBeenCalledWith("RAW_PROVIDER_REFUSAL"));
    expect(screen.getByLabelText(t("addToGroup", { name: user.email }))).toHaveValue(
      "NEW_GROUP_LITERAL"
    );
    expect(writes(ctx.requests).map((r) => r.operationName)).toEqual([names.group, names.groups]);
    expect(ctx.requests.filter((r) => r.operationName === "ClusterAuthUsers")).toHaveLength(2);
    expect(state.success).not.toHaveBeenCalledWith(t("groupsAccepted"));
  });
  it("withdraws a credential review when the actual parent permission fence changes", async () => {
    const ctx = mount(locale),
      t = tFor(locale);
    await begin(locale, "password");
    state.allowed = false;
    ctx.result.rerender(<ClusterSettingsClient slug={cluster.slug} />);
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.queryByRole("button", { name: t("addUser") })).toBeNull();
    expect(writes(ctx.requests)).toHaveLength(0);
  });
  it("requests an invitation without inventing password or confirmed delivery", async () => {
    const ctx = mount(locale),
      t = tFor(locale);
    await userEvent.click(await screen.findByRole("button", { name: t("addUser") }));
    await userEvent.type(screen.getByLabelText(t("email")), "invited@example.test");
    await userEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: t("addUser") })
    );
    await waitFor(() => expect(state.success).toHaveBeenCalledWith(t("createdInvitation")));
    expect(writes(ctx.requests)[0].variables).toEqual({
      input: {
        clusterId: cluster.id,
        expectedSource: {
          providerPluginId: observed.source.providerPluginId,
          providerPoolId: observed.source.providerPoolId,
          sourceVersion: observed.source.sourceVersion,
        },
        email: "invited@example.test",
        password: null,
        permanent: false,
        groups: [],
      },
    });
  });
});

function args(nodes: MessageFormatElement[]): string[] {
  return nodes
    .flatMap((n): string[] => {
      if (n.type === 0 || n.type === 7) return [];
      if (n.type === 8) return [`tag:${n.value}`, ...args(n.children)];
      if (n.type === 5 || n.type === 6)
        return [`${n.type}:${n.value}`, ...Object.values(n.options).flatMap((o) => args(o.value))];
      return [`${n.type}:${n.value}`];
    })
    .sort();
}
function leaves(value: Record<string, unknown>, prefix = ""): Record<string, string> {
  return Object.fromEntries(
    Object.entries(value).flatMap(([key, child]) =>
      typeof child === "string"
        ? [[prefix + key, child]]
        : Object.entries(leaves(child as Record<string, unknown>, prefix + key + "."))
    )
  );
}
describe.each(locales)("Auth-user request-local presentation in %s", (locale) => {
  it("has genuine ICU copy while preserving filter IDs and future metadata literally", () => {
    const canonical = leaves(catalogs.en.clusterSettings.authUsers),
      translated = leaves(catalogs[locale].clusterSettings.authUsers),
      t = tFor(locale);
    expect(Object.keys(translated)).toEqual(Object.keys(canonical));
    for (const [key, message] of Object.entries(canonical)) {
      expect(args(parseMessage(translated[key]))).toEqual(args(parseMessage(message)));
      if (locale !== "en" && !(locale === "de" && key === "status"))
        expect(translated[key]).not.toBe(message);
      expect(t(key, { name: "RAW_USER_LITERAL", group: "RAW_GROUP_LITERAL" })).not.toMatch(
        /\{(?:name|group)\}/
      );
    }
    const localized = localizedAuthUsersList(t);
    expect(localized.id).toBe(AUTH_USERS_LIST.id);
    expect(localized.views.map((v) => [v.key, v.filters])).toEqual(
      AUTH_USERS_LIST.views.map((v) => [v.key, v.filters])
    );
    expect(localized.fields.map((f) => [f.key, f.options?.map((o) => o.value)])).toEqual(
      AUTH_USERS_LIST.fields.map((f) => [f.key, f.options?.map((o) => o.value)])
    );
    render(
      <NextIntlClientProvider locale={locale} messages={catalogs[locale]}>
        <AuthUsersView
          {...AUTH_USERS}
          view={{
            ...AUTH_USERS.view!,
            provider: "RAW_FUTURE_PROVIDER",
            reachNote: "RAW_FUTURE_REACH",
            users: [{ ...user, status: "RAW_FUTURE_STATUS" }],
          }}
        />
      </NextIntlClientProvider>
    );
    for (const value of [
      "RAW_FUTURE_PROVIDER",
      "RAW_FUTURE_REACH",
      "RAW_FUTURE_STATUS",
      user.email,
      "GROUP_A_LITERAL",
    ])
      expect(document.body).toHaveTextContent(value);
  });
  it("preserves a same-source secret draft across locale changes and clears it across A→B→A", async () => {
    const onSet = vi.fn().mockResolvedValue(true),
      onReset = vi.fn().mockResolvedValue(true);
    function tree(currentLocale: string, sourceKey: string) {
      return (
        <NextIntlClientProvider locale={currentLocale} messages={catalogs[currentLocale]}>
          <AuthUsersView
            {...AUTH_USERS}
            sourceKey={sourceKey}
            onSetPassword={onSet}
            onResetPassword={onReset}
            view={{ ...observed }}
          />
        </NextIntlClientProvider>
      );
    }
    const result = render(tree("en", "SOURCE_A"));
    await rowMenu("en");
    await userEvent.click(screen.getByRole("menuitem", { name: tFor("en")("password") }));
    await userEvent.type(
      screen.getByLabelText(tFor("en")("newPassword")),
      "TEST_ONLY_REVIEWED_PRIVATE_DRAFT"
    );
    result.rerender(tree(locale, "SOURCE_A"));
    expect(screen.getByLabelText(tFor(locale)("newPassword"))).toHaveValue(
      "TEST_ONLY_REVIEWED_PRIVATE_DRAFT"
    );
    result.rerender(tree(locale, "SOURCE_B"));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    result.rerender(tree(locale, "SOURCE_A"));
    await rowMenu(locale);
    await userEvent.click(screen.getByRole("menuitem", { name: tFor(locale)("password") }));
    expect(screen.getByLabelText(tFor(locale)("newPassword"))).toHaveValue("");
    await userEvent.keyboard("{Escape}");
    expect(onSet).not.toHaveBeenCalled();
    expect(onReset).not.toHaveBeenCalled();
  });
  it("hydrates observed inventory with the same request locale and no invented full coverage", async () => {
    const recoverable = vi.fn(),
      container = document.createElement("div");
    document.body.appendChild(container);
    const tree = (
      <NextIntlClientProvider
        locale={locale}
        messages={catalogs[locale]}
        now={new Date("2026-10-01T12:00:00Z")}
        timeZone="America/Costa_Rica"
      >
        <AuthUsersView {...AUTH_USERS} />
      </NextIntlClientProvider>
    );
    container.innerHTML = renderToString(tree);
    const presentation = () => {
      const copy = container.cloneNode(true) as HTMLElement;
      copy.querySelectorAll('[data-slot="select-value"]').forEach((node) => node.remove());
      return copy.textContent;
    };
    const before = presentation();
    let root: ReturnType<typeof hydrateRoot> | undefined;
    try {
      await act(async () => {
        root = hydrateRoot(container, tree, { onRecoverableError: recoverable });
      });
      expect(presentation()).toBe(before);
      expect(recoverable).not.toHaveBeenCalled();
      expect(container).toHaveTextContent(tFor(locale)("inventoryLimit"));
    } finally {
      await act(async () => root?.unmount());
      container.remove();
    }
  });
});
