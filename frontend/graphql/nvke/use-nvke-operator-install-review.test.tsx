import {
  ApolloClient,
  ApolloLink,
  InMemoryCache,
  Observable,
  type Operation,
} from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, renderHook, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { expect, it, vi } from "vitest";
import messages from "@/messages/en.json";
import { INSTALL_REVIEW_PROPS } from "@/components/screens/nvke/NvkeOperatorInstallReviewPanel.stories";
import { OPERATOR_JOB_PROPS } from "@/components/screens/nvke/NvkeOperatorJobPanel.stories";
import { useNvkeOperatorInstallReview } from "./use-nvke-operator-install-review";
function args() {
  return {
    context: {
      scope: {
        server: "https://astro.example.test/app/gql/config/",
        organizationId: INSTALL_REVIEW_PROPS.site.id,
        actorId: 41,
      },
      scopeKey: "actor-visit",
      access: { read: true, admin: true, manage: false, loading: false },
      loading: false,
      failed: false,
    },
    source: { ...OPERATOR_JOB_PROPS, operation: INSTALL_REVIEW_PROPS.operation },
    pending: false,
    onRefresh: vi.fn(),
  };
}
function harness() {
  const calls: { request: Operation; reply: (data: Record<string, unknown>) => void }[] = [];
  const cache = new InMemoryCache();
  const client = new ApolloClient({
    cache,
    link: new ApolloLink(
      (request) =>
        new Observable((observer) => {
          calls.push({
            request,
            reply: (data) => {
              observer.next({ data });
              observer.complete();
            },
          });
        })
    ),
  });
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
      <ApolloProvider client={client}>{children}</ApolloProvider>
    </NextIntlClientProvider>
  );
  return { calls, cache, wrapper };
}
const review = {
  ...INSTALL_REVIEW_PROPS.review!,
  snapshotExpiresAt: new Date(Date.now() + 600000).toISOString(),
};
const page = {
  snapshotId: review.snapshotId,
  operationId: review.operationId,
  operationVersion: review.operationVersion,
  reviewDigest: review.reviewDigest,
  items: INSTALL_REVIEW_PROPS.table.rows,
  totalCount: 2,
  nextCursor: null,
};
function reviewReply(h: ReturnType<typeof harness>, fields = {}) {
  h.calls
    .findLast((call) => call.request.operationName === "ReviewNvkeOperatorInstall")!
    .reply({ reviewNvkeOperatorInstall: { ok: true, errors: [], data: { ...review, ...fields } } });
}
it("performs no implicit review; uses actor and original version pins; pages only the prepared snapshot without caching", async () => {
  const h = harness(),
    p = args();
  const r = renderHook(() => useNvkeOperatorInstallReview(p), { wrapper: h.wrapper });
  expect(h.calls).toHaveLength(0);
  let pending: Promise<boolean>;
  act(() => {
    pending = r.result.current!.onReview();
  });
  await waitFor(() => expect(h.calls).toHaveLength(1));
  expect(h.calls[0].request.variables.input).toEqual({
    operationId: review.operationId,
    expectedActorId: 41,
    expectedSiteVersion: review.siteVersion,
    expectedAuthorityVersion: review.authorityVersion,
    expectedOperationVersion: review.operationVersion,
  });
  expect(h.calls[0].request.getContext().expectedNvkeOrganization).toBe(
    p.context.scope.organizationId
  );
  await act(async () => {
    reviewReply(h);
    await pending;
  });
  await waitFor(() => expect(h.calls).toHaveLength(2));
  expect(h.calls[1].request.operationName).toBe("GetNvkeOperatorInstallWorkloadPage");
  expect(h.calls[1].request.variables.snapshotId).toBe(review.snapshotId);
  act(() =>
    h.calls[1].reply({
      astroliftNvkeOperatorInstallWorkloadPage: { ok: true, errors: [], data: page },
    })
  );
  await waitFor(() => expect(r.result.current!.table.rows).toHaveLength(2));
  expect(r.result.current!.table.rows[0].generation).toBe("9007199254740993");
  expect(h.cache.extract()).toEqual({});
  act(() => r.result.current!.table.refetch());
  await waitFor(() => expect(h.calls).toHaveLength(3));
  expect(
    h.calls.filter((call) => call.request.operationName === "ReviewNvkeOperatorInstall")
  ).toHaveLength(1);
});
it.each(["actor", "permission", "source", "visit"])(
  "discards a delayed review after %s changes, including returning to the old scope",
  async (kind) => {
    const h = harness(),
      original = args();
    const r = renderHook((p) => useNvkeOperatorInstallReview(p), {
      wrapper: h.wrapper,
      initialProps: original,
    });
    let pending: Promise<boolean>;
    act(() => {
      pending = r.result.current!.onReview();
    });
    await waitFor(() => expect(h.calls).toHaveLength(1));
    const changed = args();
    if (kind === "actor") changed.context.scope.actorId = 42;
    if (kind === "permission") changed.context.access.admin = false;
    if (kind === "source")
      changed.source.operation = {
        ...changed.source.operation,
        version: changed.source.operation.version + 1,
      };
    if (kind === "visit") changed.context.scopeKey = "another-visit";
    r.rerender(changed);
    r.rerender(original);
    await act(async () => {
      reviewReply(h);
      expect(await pending).toBe(false);
    });
    expect(r.result.current!.review).toBeNull();
    expect(h.calls).toHaveLength(1);
  }
);
it.each([
  { continuationAuthorized: true },
  { instanceId: "123e4567-e89b-12d3-a456-426614174099" },
  { snapshotExpiresAt: "2020-01-01T00:00:00Z" },
])("rejects a malformed or mismatched review %j before paging", async (fields) => {
  const h = harness(),
    r = renderHook(() => useNvkeOperatorInstallReview(args()), { wrapper: h.wrapper });
  let pending: Promise<boolean>;
  act(() => {
    pending = r.result.current!.onReview();
  });
  await waitFor(() => expect(h.calls).toHaveLength(1));
  await act(async () => {
    reviewReply(h, fields);
    expect(await pending).toBe(false);
  });
  expect(r.result.current!.review).toBeNull();
  expect(r.result.current!.error).toBe(messages.nvke.requestFailed);
  expect(h.calls).toHaveLength(1);
});
it("rejects a page from another snapshot and exposes no rows", async () => {
  const h = harness(),
    r = renderHook(() => useNvkeOperatorInstallReview(args()), { wrapper: h.wrapper });
  let pending: Promise<boolean>;
  act(() => {
    pending = r.result.current!.onReview();
  });
  await waitFor(() => expect(h.calls).toHaveLength(1));
  await act(async () => {
    reviewReply(h);
    await pending;
  });
  await waitFor(() => expect(h.calls).toHaveLength(2));
  act(() =>
    h.calls[1].reply({
      astroliftNvkeOperatorInstallWorkloadPage: {
        ok: true,
        errors: [],
        data: { ...page, reviewDigest: "f".repeat(64) },
      },
    })
  );
  await waitFor(() => expect(r.result.current!.table.state).toBe("error"));
  expect(r.result.current!.table.rows).toHaveLength(0);
});
it("walks opaque server cursors and drops all observations on permission loss", async () => {
  const h = harness(),
    original = args();
  const r = renderHook((p) => useNvkeOperatorInstallReview(p), {
    wrapper: h.wrapper,
    initialProps: original,
  });
  let pending: Promise<boolean>;
  act(() => {
    pending = r.result.current!.onReview();
  });
  await waitFor(() => expect(h.calls).toHaveLength(1));
  await act(async () => {
    reviewReply(h, { workloadCount: 113 });
    await pending;
  });
  await waitFor(() => expect(h.calls).toHaveLength(2));
  act(() =>
    h.calls[1].reply({
      astroliftNvkeOperatorInstallWorkloadPage: {
        ok: true,
        errors: [],
        data: { ...page, totalCount: 113, nextCursor: "opaque-page-2" },
      },
    })
  );
  await waitFor(() => expect(r.result.current!.table.rows).toHaveLength(2));
  act(() => r.result.current!.table.next());
  await waitFor(() => expect(h.calls).toHaveLength(3));
  expect(h.calls[2].request.variables.after).toBe("opaque-page-2");
  expect(h.calls[2].request.variables.snapshotId).toBe(review.snapshotId);
  const revoked = args();
  revoked.context.access.admin = false;
  r.rerender(revoked);
  expect(r.result.current!.review).toBeNull();
  expect(r.result.current!.canReview).toBe(false);
  act(() =>
    h.calls[2].reply({
      astroliftNvkeOperatorInstallWorkloadPage: {
        ok: true,
        errors: [],
        data: { ...page, totalCount: 113 },
      },
    })
  );
  r.rerender(original);
  expect(r.result.current!.review).toBeNull();
  expect(
    h.calls.filter((call) => call.request.operationName === "ReviewNvkeOperatorInstall")
  ).toHaveLength(1);
});
it("expires an in-memory review without requesting another native snapshot", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    const h = harness(),
      r = renderHook(() => useNvkeOperatorInstallReview(args()), { wrapper: h.wrapper });
    let pending: Promise<boolean>;
    act(() => {
      pending = r.result.current!.onReview();
    });
    await waitFor(() => expect(h.calls).toHaveLength(1));
    await act(async () => {
      reviewReply(h, { snapshotExpiresAt: new Date(Date.now() + 600000).toISOString() });
      await pending;
    });
    await waitFor(() => expect(h.calls).toHaveLength(2));
    act(() =>
      h.calls[1].reply({
        astroliftNvkeOperatorInstallWorkloadPage: { ok: true, errors: [], data: page },
      })
    );
    await waitFor(() => expect(r.result.current!.table.rows).toHaveLength(2));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(600001);
    });
    expect(r.result.current!.review).toBeNull();
    expect(r.result.current!.error).toBe(messages.nvke.installReview.unavailable);
    expect(h.calls).toHaveLength(2);
    r.unmount();
  } finally {
    vi.useRealTimers();
  }
});
