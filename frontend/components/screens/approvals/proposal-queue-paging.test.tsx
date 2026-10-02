import { ApolloClient, ApolloLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, render, renderHook, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { Observable } from "rxjs";
import { parse as parseIcu } from "@formatjs/icu-messageformat-parser";
import { print } from "graphql";
import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";
import { locales } from "@/i18n/config";
import messages from "@/messages/en.json";
import {
  LIST_SECRET_CHANGE_PROPOSALS_PAGE,
  GET_SECRET_CHANGE_PROPOSAL_METADATA,
} from "@/graphql/services/services.queries";
import { QUEUE, PROPOSAL, SECRET_SUMMARY, proposalMetadata } from "./approvals-b.fixtures";
import { SecretProposalsQueue } from "./SecretProposalsQueue";
import { SecretProposalsSummary } from "./SecretProposalsSummary";
import { useSecretProposalsQueue } from "./use-secret-proposals-queue";
import { invalidateSecretProposalQueue } from "./secret-proposal-queue-events";

vi.mock("@/components/list/use-list-state", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/list/use-list-state")>();
  return { ...actual, useListState: actual.useLocalListState };
});

function network(total = 237) {
  const calls: Record<string, unknown>[] = [];
  let changed = false;
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new ApolloLink(
      (operation) =>
        new Observable((observer) => {
          calls.push(operation.variables);
          const start = Number(operation.variables.after ?? 0),
            limit = Number(operation.variables.limit);
          const timer = setTimeout(() => {
            if (changed && start)
              observer.next({
                errors: [
                  {
                    message: "Queue changed; restart the walk.",
                    extensions: { code: "STALE_CURSOR" },
                  },
                ],
              });
            else {
              const end = Math.min(start + limit, total);
              observer.next({
                data: {
                  astroliftSecretChangeProposalsPage: {
                    __typename: "AstroliftSecretChangeProposalPage",
                    items: Array.from({ length: end - start }, (_, i) => ({
                      ...proposalMetadata(PROPOSAL),
                      id: `proposal-${start + i}`,
                    })),
                    nextCursor: end < total ? String(end) : null,
                    totalCount: total,
                    complete: end === total,
                  },
                },
              });
            }
            observer.complete();
          }, 1);
          return () => clearTimeout(timer);
        })
    ),
  });
  return {
    client,
    calls,
    change: () => {
      changed = true;
    },
    wrapper: ({ children }: { children: React.ReactNode }) => (
      <ApolloProvider client={client}>{children}</ApolloProvider>
    ),
  };
}

it("walks beyond 200 using server cursors without duplicate or skipped rows", async () => {
  const transport = network(),
    hook = renderHook(useSecretProposalsQueue, { wrapper: transport.wrapper });
  const ids: string[] = [];
  for (let index = 0; index < 10; index++) {
    await waitFor(() => expect(hook.result.current.loading).toBe(false));
    await waitFor(() => expect(hook.result.current.stale).toBe(false));
    ids.push(...hook.result.current.rows.map((row) => row.id));
    if (!hook.result.current.nextCursor) break;
    act(() => hook.result.current.list.older(hook.result.current.nextCursor!));
  }
  expect(ids).toHaveLength(237);
  expect(new Set(ids).size).toBe(237);
  expect(hook.result.current.totalCount).toBe(237);
  expect(transport.calls.every((call) => call.limit === 25 && call.status === "pending")).toBe(
    true
  );
  expect(transport.calls.some((call) => Number(call.after) > 200)).toBe(true);
  hook.unmount();
  transport.client.stop();
});

it.each(["refresh", "decision"])("invalidates the entire cursor chain after %s", async (mode) => {
  const transport = network(),
    hook = renderHook(useSecretProposalsQueue, { wrapper: transport.wrapper });
  await waitFor(() => expect(hook.result.current.rows[0]?.id).toBe("proposal-0"));
  act(() => hook.result.current.list.older(hook.result.current.nextCursor!));
  await waitFor(() => expect(hook.result.current.rows[0]?.id).toBe("proposal-25"));
  act(() =>
    mode === "decision" ? invalidateSecretProposalQueue() : hook.result.current.onRefresh()
  );
  await waitFor(() => expect(hook.result.current.rows[0]?.id).toBe("proposal-0"));
  expect(hook.result.current.list.state.after).toBeNull();
  expect(hook.result.current.list.hasNewer).toBe(false);
  expect(transport.calls.at(-1)?.after ?? null).toBeNull();
  hook.unmount();
  transport.client.stop();
});

it("retains a stale-cursor refusal and retries from the first page", async () => {
  const transport = network(),
    hook = renderHook(useSecretProposalsQueue, { wrapper: transport.wrapper });
  await waitFor(() => expect(hook.result.current.rows[0]?.id).toBe("proposal-0"));
  transport.change();
  act(() => hook.result.current.list.older(hook.result.current.nextCursor!));
  await waitFor(() => expect(hook.result.current.error).toBeTruthy());
  expect(hook.result.current.error?.message).toContain("Queue changed");
  expect(hook.result.current.totalCount).toBeNull();
  expect(hook.result.current.nextCursor).toBeNull();
  act(() => hook.result.current.onRetry());
  await waitFor(() => expect(hook.result.current.loading).toBe(false));
  expect(hook.result.current.list.state.after).toBeNull();
  expect(hook.result.current.rows[0].id).toBe("proposal-0");
  hook.unmount();
  transport.client.stop();
});

it("renders metadata and exact links without requesting diff summaries or value hints", () => {
  render(
    <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
      <SecretProposalsQueue {...QUEUE} />
    </NextIntlClientProvider>
  );
  expect(screen.getByRole("link", { name: /billing-api/ })).toHaveAttribute(
    "href",
    "/approvals/secret/prop-1"
  );
  expect(screen.queryByText("Rotate DATABASE_URL")).not.toBeInTheDocument();
  expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  for (const operation of [LIST_SECRET_CHANGE_PROPOSALS_PAGE, GET_SECRET_CHANGE_PROPOSAL_METADATA])
    expect(print(operation)).not.toMatch(/payload|payloadDiff|applyError|reason|valueMasked/);
});

describe.each(locales)("proposal queue copy in %s", (locale) => {
  it("has complete valid ICU messages and translates recovery", () => {
    const catalog = JSON.parse(readFileSync(`messages/${locale}.json`, "utf8")).lists
      .secretProposalsQueue.paging;
    expect(Object.keys(catalog).sort()).toEqual(
      Object.keys(messages.lists.secretProposalsQueue.paging).sort()
    );
    for (const message of Object.values(catalog))
      expect(() => parseIcu(message as string)).not.toThrow();
    if (locale !== "en")
      expect(catalog.recovery).not.toEqual(messages.lists.secretProposalsQueue.paging.recovery);
  });
});

it("shows a refused summary read instead of retaining previously readable rows", () => {
  render(
    <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
      <SecretProposalsSummary
        {...SECRET_SUMMARY}
        count={null}
        error={{ name: "Error", message: "Permission denied" }}
      />
    </NextIntlClientProvider>
  );
  expect(screen.getByText("Permission denied")).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: /billing-api/ })).not.toBeInTheDocument();
});
