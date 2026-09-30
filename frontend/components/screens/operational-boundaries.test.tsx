import { ApolloClient, ApolloLink, InMemoryCache, type Operation } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { Observable, type Subscriber } from "rxjs";
import { afterEach, describe, expect, it, vi } from "vitest";

import messages from "@/messages/en.json";
import { AgentOverviewView } from "./agents/detail/AgentOverview";
import { OVERVIEW } from "./agents/detail/agent-detail-shell.fixtures";
import { COMPLETED_TASK } from "./agents/runs/agent-runs.fixtures";
import { useAgentRunDetail } from "./agents/runs/use-agent-run-detail";
import { CommandRunnerScreen } from "./apps/tools/CommandRunnerScreen";
import { COMMAND_RUNNER } from "./apps/tools/app-observability-shell-topology-commands.fixtures";
import { useAppSecrets } from "./apps/secrets/use-app-secrets";
import { SECRETS } from "./apps/secrets/app-secrets-tokens.fixtures";
import { useHelp } from "./documentation/use-help";
import { useWebhooks } from "./webhooks/use-webhooks";
import { SUBSCRIPTIONS } from "./webhooks/webhooks-zentinelle.fixtures";

vi.mock("next/navigation", () => ({
  usePathname: () => "/webhooks",
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));
vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({ org: { id: "org", slug: "demo", name: "Demo" } }),
}));
vi.mock("@/components/PageShell", () => ({
  PageShell: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}));

function context(
  reply: (operation: Operation, observer: Subscriber<{ data: Record<string, unknown> }>) => void
) {
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new ApolloLink((operation) => new Observable((observer) => reply(operation, observer))),
  });
  return {
    client,
    wrapper: ({ children }: { children: ReactNode }) => (
      <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
        <ApolloProvider client={client}>{children}</ApolloProvider>
      </NextIntlClientProvider>
    ),
  };
}
const complete = (
  observer: Subscriber<{ data: Record<string, unknown> }>,
  data: Record<string, unknown>
) => {
  observer.next({ data });
  observer.complete();
};
afterEach(() => vi.useRealTimers());

it("does not offer a command when its container target is missing", () => {
  const onRun = vi.fn();
  const view = render(
    <CommandRunnerScreen {...COMMAND_RUNNER} command="ls" containerName="" onRun={onRun} />
  );
  const run = screen.getByRole("button", { name: "Run" });
  expect(run).toBeDisabled();
  fireEvent.click(run);
  expect(onRun).not.toHaveBeenCalled();
  view.rerender(<CommandRunnerScreen {...COMMAND_RUNNER} command="ls" onRun={onRun} />);
  expect(run).toBeEnabled();
  fireEvent.click(run);
  expect(onRun).toHaveBeenCalledOnce();
});

it("offers overseer input for uppercase running runs", () => {
  render(
    <AgentOverviewView
      {...OVERVIEW}
      fleet={null}
      runs={{ ...OVERVIEW.runs, rows: [{ ...OVERVIEW.runs.rows[0], status: "RUNNING" }] }}
    />
  );
  expect(screen.getByPlaceholderText("Send a follow-up to the running agent…")).toBeVisible();
});

describe("terminal agent polling", () => {
  it.each(["succeeded", "canceled", "CANCELLED", "COMPLETED", "TIMED_OUT"])(
    "stops task and log polling for %s",
    async (status) => {
      vi.useFakeTimers();
      const requests: string[] = [];
      const transport = context((operation, observer) => {
        requests.push(operation.operationName ?? "");
        complete(
          observer,
          operation.operationName === "GetAgentTask"
            ? { agentTask: { ...COMPLETED_TASK, status } }
            : { agentTaskLogs: [] }
        );
      });
      const hook = renderHook(() => useAgentRunDetail(COMPLETED_TASK.id), {
        wrapper: transport.wrapper,
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1);
      });
      expect(hook.result.current.terminal).toBe(true);
      const initial = [...requests];
      await act(async () => {
        await vi.advanceTimersByTimeAsync(20_000);
      });
      expect(requests).toEqual(initial);
      hook.unmount();
      transport.client.stop();
    }
  );
});

it("blocks secret sheets throughout an actual rotation request", async () => {
  let mutation: Subscriber<{ data: Record<string, unknown> }> | undefined;
  const transport = context((operation, observer) => {
    if (operation.operationName === "RotateAppSecret") {
      mutation = observer;
      return;
    }
    complete(observer, {
      astroliftEnvironments: [],
      astroliftApp: { id: "app", version: 1 },
      astroliftAppSecrets: [],
      astroliftAppSecretBundleAttachments: [],
      astroliftSecretChangeProposals: [],
    });
  });
  const hook = renderHook(() => useAppSecrets("demo"), { wrapper: transport.wrapper });
  await act(async () => hook.result.current.onStartRotate(SECRETS[0]));
  let saving: Promise<boolean>;
  act(() => {
    saving = hook.result.current.onInlineSave(SECRETS[0], "replacement");
  });
  await waitFor(() => expect(mutation).toBeDefined());
  expect(hook.result.current.busy).toBe(true);
  await act(async () => {
    complete(mutation!, {
      rotateAppSecret: { ok: false, errors: [{ message: "Denied" }], data: null },
    });
    await saving!;
  });
  expect(hook.result.current.busy).toBe(false);
  hook.unmount();
  transport.client.stop();
});

it("tracks pending webhooks by actual row and releases them after success or transport failure", async () => {
  const pending = new Map<string, Subscriber<{ data: Record<string, unknown> }>>();
  const writes: string[] = [];
  const transport = context((operation, observer) => {
    if (operation.operationName === "DeleteWebhook") {
      const id = operation.variables.input.id;
      writes.push(id);
      pending.set(id, observer);
      return;
    }
    complete(observer, {
      astroliftWebhookSubscriptionsPage: { items: [], totalCount: 0, nextCursor: null },
    });
  });
  const hook = renderHook(() => useWebhooks("demo"), { wrapper: transport.wrapper });
  let first: Promise<void>, second: Promise<void>;
  act(() => {
    first = hook.result.current.onDelete(SUBSCRIPTIONS[0]);
    second = hook.result.current.onDelete(SUBSCRIPTIONS[1]);
  });
  expect(hook.result.current.pendingRows).toEqual(
    new Set([SUBSCRIPTIONS[0].id, SUBSCRIPTIONS[1].id])
  );
  await expect(hook.result.current.onRotate(SUBSCRIPTIONS[0])).rejects.toThrow("already running");
  expect(writes).toEqual([SUBSCRIPTIONS[0].id, SUBSCRIPTIONS[1].id]);
  const failed = expect(first!).rejects.toThrow("Network unavailable");
  await act(async () => {
    pending.get(SUBSCRIPTIONS[0].id)!.error(new Error("Network unavailable"));
    await failed;
  });
  expect(hook.result.current.pendingRows).toEqual(new Set([SUBSCRIPTIONS[1].id]));
  await act(async () => {
    complete(pending.get(SUBSCRIPTIONS[1].id)!, {
      deleteWebhookSubscription: {
        ok: true,
        errors: [],
        data: { id: SUBSCRIPTIONS[1].id, deleted: true },
      },
    });
    await second!;
  });
  expect(hook.result.current.pendingRows.size).toBe(0);
  hook.unmount();
  transport.client.stop();
});

it("replaces diagnostic copy timers and clears them on unmount", async () => {
  vi.useFakeTimers();
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: vi.fn().mockResolvedValue(undefined) },
  });
  const hook = renderHook(() => useHelp("1.0"));
  await act(() => hook.result.current.copyDiagnostics());
  act(() => vi.advanceTimersByTime(1_000));
  await act(() => hook.result.current.copyDiagnostics());
  act(() => vi.advanceTimersByTime(500));
  expect(hook.result.current.copied).toBe(true);
  act(() => vi.advanceTimersByTime(1_000));
  expect(hook.result.current.copied).toBe(false);
  await act(() => hook.result.current.copyDiagnostics());
  hook.unmount();
  expect(vi.getTimerCount()).toBe(0);
});
