import { NextIntlClientProvider } from "next-intl";
import messages from "@/messages/en.json";
import { renderWithIntl as render } from "@/test/render-with-intl";
import { ApolloClient, ApolloLink, InMemoryCache, type Operation } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, fireEvent, renderHook, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { Observable, type Subscriber } from "rxjs";
import { afterEach, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";

import { AgentSecretsView } from "./agents/list/AgentSecrets";
import { AgentSecretBundlesView } from "./agents/list/AgentSecretBundles";
import {
  ATTACHMENT,
  BUNDLES,
  BUNDLES_LIST,
  SECRET_ROWS,
  SECRETS,
} from "./agents/list/agents-dispatch-secrets.fixtures";
import { useAgentSecrets } from "./agents/list/use-agent-secrets";
import { useAgentSecretBundles } from "./agents/list/use-agent-secret-bundles";
import { EnvironmentSettingsView } from "./apps/settings/EnvironmentSettings";
import { ENV_SETTINGS } from "./apps/settings/app-settings-members.fixtures";
import { useEnvironmentSettings } from "./apps/settings/use-environment-settings";

vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({ org: { id: "org", slug: "demo", name: "Demo" } }),
}));
vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({ can: () => true, granted: new Set(["app.update"]), loading: false }),
}));
type Observer = Subscriber<{ data: Record<string, unknown> }>;
function complete(observer: Observer, data: Record<string, unknown>) {
  observer.next({ data });
  observer.complete();
}
function network(reply: (operation: Operation, observer: Observer) => void) {
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new ApolloLink((operation) => new Observable((observer) => reply(operation, observer))),
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <NextIntlClientProvider locale="en" messages={messages}>
      <ApolloProvider client={client}>{children}</ApolloProvider>
    </NextIntlClientProvider>
  );
  return { client, wrapper };
}
function reads(operation: Operation, observer: Observer) {
  if (operation.operationName === "AgentSecretBundles")
    complete(observer, { agentSecretBundles: BUNDLES_LIST });
  else if (operation.operationName === "AgentSecretBundleAttachments")
    complete(observer, { agentEnvironmentSpecSecretBundleAttachments: [ATTACHMENT] });
  else if (operation.operationName === "AgentEnvironmentSpecSecretStatus")
    complete(observer, { agentEnvironmentSpecSecretStatus: SECRET_ROWS });
  else complete(observer, { astroliftEnvironments: [] });
}
afterEach(() => {
  vi.useRealTimers();
});

it("admits one clear per environment/key, permits other rows and releases failed requests", async () => {
  const requests: Array<{ operation: Operation; observer: Observer }> = [];
  const transport = network((operation, observer) => {
    if (operation.operationName === "ClearEnvironmentSetting")
      requests.push({ operation, observer });
    else reads(operation, observer);
  });
  const hook = renderHook(() => useEnvironmentSettings("app"), { wrapper: transport.wrapper });
  await waitFor(() => expect(hook.result.current.envs).toEqual([]));
  let first: Promise<void>;
  let second: Promise<void>;
  act(() => {
    first = hook.result.current.onClear("env-a", "replicas");
    void hook.result.current.onClear("env-a", "replicas");
    second = hook.result.current.onClear("env-b", "replicas");
  });
  expect(requests).toHaveLength(2);
  expect(hook.result.current.clearing.size).toBe(2);
  await act(async () => {
    requests[0].observer.error(new Error("Refused"));
    await first;
  });
  expect(hook.result.current.clearing.has(JSON.stringify(["env-a", "replicas"]))).toBe(false);
  expect(hook.result.current.clearing.has(JSON.stringify(["env-b", "replicas"]))).toBe(true);
  await act(async () => {
    complete(requests[1].observer, {
      clearEnvironmentSetting: {
        ok: false,
        errors: [{ code: "DENIED", message: "Refused", field: null }],
        data: null,
      },
    });
    await second;
  });
  expect(hook.result.current.clearing.size).toBe(0);
  hook.unmount();
  transport.client.stop();
});

it("disables only the override being cleared", () => {
  const env = ENV_SETTINGS.envs[0];
  const key = env.settings[0].key;
  const onClear = vi.fn();
  render(
    <EnvironmentSettingsView
      {...ENV_SETTINGS}
      clearing={new Set([JSON.stringify([env.id, key])])}
      onClear={onClear}
    />,
    { wrapper: ({ children }) => <TooltipProvider>{children}</TooltipProvider> }
  );
  const busy = screen.getByRole("button", { name: `Clear ${key} override` });
  expect(busy).toBeDisabled();
  fireEvent.click(busy);
  expect(onClear).not.toHaveBeenCalled();
  expect(
    screen.getAllByTitle("Clear override").filter((button) => !button.hasAttribute("disabled"))
      .length
  ).toBeGreaterThan(0);
});

it.each(["attach", "delete-key"])(
  "guards a pending bundle %s and releases transport failures",
  async (kind) => {
    const requests: Array<{ operation: Operation; observer: Observer }> = [];
    const transport = network((operation, observer) => {
      if (
        operation.operationName === "AttachAgentSecretBundle" ||
        operation.operationName === "DeleteAgentBundleSecretValue"
      )
        requests.push({ operation, observer });
      else reads(operation, observer);
    });
    const hook = renderHook(() => useAgentSecretBundles("spec", true), {
      wrapper: transport.wrapper,
    });
    await waitFor(() => expect(hook.result.current.bundles.length).toBe(2));
    const bundle = BUNDLES_LIST[0];
    let request: Promise<unknown>;
    const invoke = () =>
      kind === "attach"
        ? hook.result.current.onAttach(bundle, "GH_", 0)
        : hook.result.current.onDeleteKey(bundle, "GITHUB_APP_ID");
    act(() => {
      request = invoke();
      void invoke();
    });
    expect(requests).toHaveLength(1);
    expect(hook.result.current.pending.size).toBe(1);
    await act(async () => {
      requests[0].observer.error(new Error("Refused"));
      await request;
    });
    expect(hook.result.current.pending.size).toBe(0);
    act(() => {
      request = invoke();
    });
    expect(requests).toHaveLength(2);
    await act(async () => {
      requests[1].observer.error(new Error("Refused again"));
      await request;
    });
    hook.unmount();
    transport.client.stop();
  }
);

it("shows disabled attachment and key-delete controls while other bundle rows stay available", () => {
  render(
    <AgentSecretBundlesView
      {...BUNDLES}
      defaultAttachments={[]}
      pending={new Set(["attach:bundle-1", "key-delete:bundle-1:GITHUB_APP_ID"])}
    />
  );
  const buttons = screen.getAllByRole("button", { name: "Attach" });
  expect(buttons[0]).toBeDisabled();
  expect(buttons[1]).toBeEnabled();
  expect(screen.getByRole("button", { name: "Delete GITHUB_APP_ID" })).toBeDisabled();
});

it("uses the trimmed ref identity for admission, variables, pending state and retries", async () => {
  const requests: Array<{ operation: Operation; observer: Observer }> = [];
  const transport = network((operation, observer) => {
    if (operation.operationName === "UpsertAgentSecretRef") requests.push({ operation, observer });
    else reads(operation, observer);
  });
  const hook = renderHook(() => useAgentSecrets("spec", true), { wrapper: transport.wrapper });
  await waitFor(() => expect(hook.result.current.rows.length).toBe(3));
  let result: Promise<boolean>;
  act(() => {
    result = hook.result.current.onUpsertRef(" API_KEY ", " agents/org/key ");
    void hook.result.current.onUpsertRef("API_KEY", "agents/org/key");
  });
  expect(requests).toHaveLength(1);
  expect(requests[0].operation.variables).toEqual({
    slug: "spec",
    envVar: "API_KEY",
    uri: "agents/org/key",
  });
  expect(hook.result.current.pending.has("ref:API_KEY")).toBe(true);
  await act(async () => {
    requests[0].observer.error(new Error("Refused"));
    expect(await result).toBe(false);
  });
  expect(hook.result.current.pending.size).toBe(0);
  hook.unmount();
  transport.client.stop();
});

it("keeps a padded reference draft and disables its pending save", () => {
  const onUpsertRef = vi.fn();
  render(
    <AgentSecretsView
      {...SECRETS}
      embedded
      pending={new Set(["ref:API_KEY"])}
      onUpsertRef={onUpsertRef}
    />
  );
  fireEvent.change(screen.getByPlaceholderText("ENV_VAR"), { target: { value: " API_KEY " } });
  fireEvent.change(screen.getByPlaceholderText(/Provider URI/), {
    target: { value: "agents/org/key" },
  });
  const save = screen.getByRole("button", { name: "Add / update ref" });
  expect(save).toBeDisabled();
  fireEvent.click(save);
  expect(onUpsertRef).not.toHaveBeenCalled();
  expect(screen.getByPlaceholderText("ENV_VAR")).toHaveValue(" API_KEY ");
});

const revealData = {
  revealAgentSecretValue: {
    ok: true,
    errors: [],
    data: {
      envVar: SECRET_ROWS[0].envVar,
      uri: SECRET_ROWS[0].uri,
      value: "revealed-test-value",
      provider: SECRET_ROWS[0].provider,
      revealedAt: "2026-09-30T12:00:00Z",
    },
  },
};
it.each(["close", "scope", "unmount"])(
  "discards an in-flight reveal after %s",
  async (boundary) => {
    let observer: Observer | undefined;
    const transport = network((operation, incoming) => {
      if (operation.operationName === "RevealAgentSecretValue") observer = incoming;
      else reads(operation, incoming);
    });
    const hook = renderHook(({ slug, open }) => useAgentSecrets(slug, open), {
      initialProps: { slug: "spec", open: true },
      wrapper: transport.wrapper,
    });
    await waitFor(() => expect(hook.result.current.rows.length).toBe(3));
    let request: Promise<void>;
    act(() => {
      request = hook.result.current.onReveal(SECRET_ROWS[0]);
    });
    if (boundary === "close") hook.rerender({ slug: "spec", open: false });
    else if (boundary === "scope") hook.rerender({ slug: "other-spec", open: true });
    else hook.unmount();
    await act(async () => {
      complete(observer!, revealData);
      await request;
    });
    expect(hook.result.current.reveals).toEqual({});
    if (boundary !== "unmount") hook.unmount();
    transport.client.stop();
  }
);

it("cancels reveal timers on clear, so an earlier timer cannot hide a later reveal", async () => {
  vi.useFakeTimers();
  const transport = network((operation, observer) => {
    if (operation.operationName === "RevealAgentSecretValue") complete(observer, revealData);
    else reads(operation, observer);
  });
  const hook = renderHook(() => useAgentSecrets("spec", true), { wrapper: transport.wrapper });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1);
  });
  await act(async () => {
    await hook.result.current.onReveal(SECRET_ROWS[0]);
  });
  expect(hook.result.current.reveals[SECRET_ROWS[0].envVar]).toBe("revealed-test-value");
  await act(async () => {
    await vi.advanceTimersByTimeAsync(20_000);
    hook.result.current.clearReveals();
  });
  expect(hook.result.current.reveals).toEqual({});
  await act(async () => {
    await hook.result.current.onReveal(SECRET_ROWS[0]);
    await vi.advanceTimersByTimeAsync(10_001);
  });
  expect(hook.result.current.reveals[SECRET_ROWS[0].envVar]).toBe("revealed-test-value");
  await act(async () => {
    await vi.advanceTimersByTimeAsync(20_000);
  });
  expect(hook.result.current.reveals).toEqual({});
  hook.unmount();
  transport.client.stop();
});

it("clears an already revealed value when the parent closes or changes the spec", async () => {
  const transport = network((operation, observer) => {
    if (operation.operationName === "RevealAgentSecretValue") complete(observer, revealData);
    else reads(operation, observer);
  });
  const hook = renderHook(({ slug, open }) => useAgentSecrets(slug, open), {
    initialProps: { slug: "spec", open: true },
    wrapper: transport.wrapper,
  });
  await waitFor(() => expect(hook.result.current.rows.length).toBe(3));
  await act(async () => {
    await hook.result.current.onReveal(SECRET_ROWS[0]);
  });
  expect(hook.result.current.reveals[SECRET_ROWS[0].envVar]).toBe("revealed-test-value");
  hook.rerender({ slug: "spec", open: false });
  hook.rerender({ slug: "spec", open: true });
  expect(hook.result.current.reveals).toEqual({});
  await act(async () => {
    await hook.result.current.onReveal(SECRET_ROWS[0]);
  });
  hook.rerender({ slug: "other-spec", open: true });
  expect(hook.result.current.reveals).toEqual({});
  hook.unmount();
  transport.client.stop();
});

it("keeps a successful clear pending until its environment refetch finishes", async () => {
  let mutation: Observer | undefined;
  let refresh: Observer | undefined;
  let queries = 0;
  const transport = network((operation, observer) => {
    if (operation.operationName === "ClearEnvironmentSetting") mutation = observer;
    else if (++queries === 1) reads(operation, observer);
    else refresh = observer;
  });
  const hook = renderHook(() => useEnvironmentSettings("app"), { wrapper: transport.wrapper });
  await waitFor(() => expect(queries).toBe(1));
  let request: Promise<void>;
  act(() => {
    request = hook.result.current.onClear("env-a", "replicas");
  });
  await act(async () => {
    complete(mutation!, { clearEnvironmentSetting: { ok: true, errors: [], data: null } });
  });
  await waitFor(() => expect(refresh).toBeTruthy());
  expect(hook.result.current.clearing.has(JSON.stringify(["env-a", "replicas"]))).toBe(true);
  await act(async () => {
    complete(refresh!, { astroliftEnvironments: [] });
    await request;
  });
  expect(hook.result.current.clearing.size).toBe(0);
  hook.unmount();
  transport.client.stop();
});

it("keeps a changed reference draft when an earlier save completes", async () => {
  let resolve: (success: boolean) => void = () => {};
  const onUpsertRef = vi.fn(
    () =>
      new Promise<boolean>((done) => {
        resolve = done;
      })
  );
  render(<AgentSecretsView {...SECRETS} embedded onUpsertRef={onUpsertRef} />);
  const name = screen.getByPlaceholderText("ENV_VAR");
  const uri = screen.getByPlaceholderText(/Provider URI/);
  fireEvent.change(name, { target: { value: "FIRST_KEY" } });
  fireEvent.change(uri, { target: { value: "agents/org/first" } });
  fireEvent.click(screen.getByRole("button", { name: "Add / update ref" }));
  expect(onUpsertRef).toHaveBeenCalledWith("FIRST_KEY", "agents/org/first");
  fireEvent.change(name, { target: { value: "SECOND_KEY" } });
  fireEvent.change(uri, { target: { value: "agents/org/second" } });
  await act(async () => {
    resolve(true);
  });
  expect(name).toHaveValue("SECOND_KEY");
  expect(uri).toHaveValue("agents/org/second");
});
