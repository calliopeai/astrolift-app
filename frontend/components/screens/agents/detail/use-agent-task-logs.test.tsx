import { ApolloClient, ApolloLink, InMemoryCache, Observable } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { NextIntlClientProvider } from "next-intl";
import en from "@/messages/en.json";
import { describe, expect, it } from "vitest";

import type { AstroliftAgentTaskLogPage } from "@/graphql/__generated__/schema";

import { useAgentTaskLogs } from "./use-agent-task-logs";

function page(messages: string[], nextCursor: string | null): AstroliftAgentTaskLogPage {
  return {
    items: messages.map((message, index) => ({
      id: message + index,
      timestamp: "2026-09-01T00:00:00Z",
      level: "info",
      stream: message === "failure" ? "stderr" : "stdout",
      message,
      podName: "task-pod",
      container: "agent",
    })),
    nextCursor,
    hasMore: nextCursor !== null,
    pageSize: 200,
    liveOnly: true,
    windowLimited: false,
    expiresAt: "2026-09-01T00:05:00Z",
  };
}

function server(
  read: (cursor: string | null) => AstroliftAgentTaskLogPage | Promise<AstroliftAgentTaskLogPage>
) {
  const requests: Array<Record<string, unknown>> = [];
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new ApolloLink(
      (operation) =>
        new Observable((observer) => {
          requests.push(operation.variables);
          queueMicrotask(async () => {
            try {
              observer.next({
                data: {
                  agentTaskLogsPage: await read(operation.variables.cursor as string | null),
                },
              });
              observer.complete();
            } catch (error) {
              observer.error(error);
            }
          });
        })
    ),
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <NextIntlClientProvider locale="en" messages={en}>
      <ApolloProvider client={client}>{children}</ApolloProvider>
    </NextIntlClientProvider>
  );
  return { client, requests, wrapper };
}

describe("Observe task log pages", () => {
  it("keeps a refreshed snapshot when an earlier-page response arrives late", async () => {
    let completeEarlier: (result: AstroliftAgentTaskLogPage) => void = () => {};
    const earlier = new Promise<AstroliftAgentTaskLogPage>((resolve) => {
      completeEarlier = resolve;
    });
    let refreshed = false;
    const source = server((cursor) =>
      cursor ? earlier : page([refreshed ? "fresh" : "old"], "earlier")
    );
    const hook = renderHook(() => useAgentTaskLogs("task-123"), { wrapper: source.wrapper });
    await waitFor(() => expect(hook.result.current.lines).toHaveLength(1));
    act(() => hook.result.current.onLoadEarlier());
    await waitFor(() => expect(source.requests).toHaveLength(2));
    refreshed = true;
    act(() => hook.result.current.onRefresh());
    await waitFor(() => expect(hook.result.current.lines[0]?.message).toBe("fresh"));
    await act(async () => {
      completeEarlier(page(["earlier"], null));
      await earlier;
    });
    expect(hook.result.current.lines.map((line) => line.message)).toEqual(["fresh"]);
    expect(hook.result.current.loadingEarlier).toBe(false);
    hook.unmount();
    source.client.stop();
  });

  it("requests earlier server pages, prepends their lines, then refreshes the latest window", async () => {
    let refreshed = false;
    const source = server((cursor) =>
      cursor === "earlier"
        ? page(["first", "first"], null)
        : page(refreshed ? ["latest"] : ["last", "failure"], "earlier")
    );
    const hook = renderHook(() => useAgentTaskLogs("task-123"), { wrapper: source.wrapper });
    await waitFor(() => expect(hook.result.current.lines).toHaveLength(2));
    expect(hook.result.current.lines[1]?.message).toBe("[stderr] failure");
    expect(hook.result.current.liveOnly).toBe(true);
    act(() => hook.result.current.onLoadEarlier());
    await waitFor(() => expect(hook.result.current.lines).toHaveLength(4));
    expect(hook.result.current.lines.map((line) => line.message)).toEqual([
      "first",
      "first",
      "last",
      "[stderr] failure",
    ]);
    expect(hook.result.current.hasMore).toBe(false);
    refreshed = true;
    act(() => hook.result.current.onRefresh());
    await waitFor(() =>
      expect(hook.result.current.lines.map((line) => line.message)).toEqual(["latest"])
    );
    expect(source.requests.map((request) => request.cursor)).toEqual([null, "earlier", null]);
    expect(
      source.requests.every((request) => request.id === "task-123" && request.limit === 200)
    ).toBe(true);
    hook.unmount();
    source.client.stop();
  });

  it("keeps displayed output and exposes expiry when the earlier page is unavailable", async () => {
    const source = server((cursor) => {
      if (cursor) throw new Error("Task log page expired; refresh the log.");
      return page(["visible"], "expired");
    });
    const hook = renderHook(() => useAgentTaskLogs("task-123"), { wrapper: source.wrapper });
    await waitFor(() => expect(hook.result.current.lines).toHaveLength(1));
    act(() => hook.result.current.onLoadEarlier());
    await waitFor(() => expect(hook.result.current.pageError).toContain("expired"));
    expect(hook.result.current.lines[0]?.message).toBe("visible");
    expect(hook.result.current.loadingEarlier).toBe(false);
    hook.unmount();
    source.client.stop();
  });
});
