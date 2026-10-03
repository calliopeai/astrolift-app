import { ApolloClient, ApolloLink, InMemoryCache, type Operation } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { Observable } from "rxjs";
import { act, fireEvent, renderHook, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { beforeEach, expect, it, vi } from "vitest";
import { toast } from "sonner";
import messages from "@/messages/en.json";
import { ConfirmProvider } from "@/hooks/use-confirm";
import { renderWithIntl as render } from "@/test/render-with-intl";
import { useLocalListState } from "@/components/list/use-list-state";
import { WorkflowInstancesScreen } from "./WorkflowInstancesScreen";
import { WORKFLOW_INSTANCES_LIST } from "./workflow-instances-list";
import { INSTANCES } from "./workflows-list.fixtures";
import {
  useInstanceAdminControls,
  useWorkflowInstancesList,
  useWorkflowInstanceDetailPanel,
} from "./use-workflow-instances";

let params = new URLSearchParams();
const replace = vi.fn();
vi.mock("next/navigation", () => ({
  usePathname: () => "/platform-activity",
  useSearchParams: () => params,
  useRouter: () => ({ replace }),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
function transport(reply: (op: Operation) => Record<string, unknown> | Error) {
  const requests: Array<{ name: string; variables: Record<string, unknown> }> = [];
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new ApolloLink(
      (op) =>
        new Observable((observer) => {
          requests.push({ name: op.operationName!, variables: structuredClone(op.variables) });
          const response = reply(op);
          queueMicrotask(() => {
            if (response instanceof Error) observer.error(response);
            else {
              observer.next(response);
              observer.complete();
            }
          });
        })
    ),
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
      <ApolloProvider client={client}>
        <ConfirmProvider>{children}</ConfirmProvider>
      </ApolloProvider>
    </NextIntlClientProvider>
  );
  return { requests, wrapper, client };
}
beforeEach(() => {
  params = new URLSearchParams();
  vi.clearAllMocks();
});

it("continues empty authorized pages using the server cursor and resets pagination for filters", async () => {
  const api = transport((op) => ({
    data: {
      astroliftWorkflowInstances: op.variables.after
        ? { items: [INSTANCES[0]], nextCursor: null }
        : { items: [], nextCursor: "next-page" },
    },
  }));
  const hook = renderHook(() => useWorkflowInstancesList(), { wrapper: api.wrapper });
  await waitFor(() => expect(hook.result.current.nextCursor).toBe("next-page"));
  act(() => hook.result.current.list.older("next-page"));
  expect(replace).toHaveBeenLastCalledWith("/platform-activity?after=next-page", { scroll: false });
  params = new URLSearchParams({ after: "next-page" });
  hook.rerender();
  await waitFor(() => expect(hook.result.current.rows).toEqual([INSTANCES[0]]));
  expect(api.requests[1].variables).toMatchObject({ after: "next-page", limit: 25 });
  expect(hook.result.current.instanceHref(INSTANCES[0])).toContain(
    `instanceRun=${INSTANCES[0].runId}`
  );
  act(() => hook.result.current.list.setFilter("status", "running"));
  expect(replace.mock.calls.at(-1)?.[0]).not.toContain("after=");
  api.client.stop();
});
it("refuses repeated cursors and shows refresh failures despite cached rows", async () => {
  params = new URLSearchParams({ after: "same" });
  let failed = false;
  const api = transport(() =>
    failed
      ? new Error("private transport body")
      : { data: { astroliftWorkflowInstances: { items: [INSTANCES[0]], nextCursor: "same" } } }
  );
  const hook = renderHook(() => useWorkflowInstancesList(), { wrapper: api.wrapper });
  await waitFor(() => expect(hook.result.current.error?.message).toContain("repeated page cursor"));
  expect(hook.result.current.nextCursor).toBeNull();
  failed = true;
  act(() => hook.result.current.onRetry());
  await waitFor(() => expect(hook.result.current.error?.message).toContain("Could not refresh"));
  expect(hook.result.current.error?.message).not.toContain("private");
  api.client.stop();
});
it("renders Older even with no visible rows and hides unsupported search", () => {
  function EmptyPage() {
    const list = useLocalListState(WORKFLOW_INSTANCES_LIST);
    return (
      <WorkflowInstancesScreen
        access="granted"
        list={list}
        rows={[]}
        totalCount={0}
        nextCursor="more"
        loading={false}
        stale={false}
        error={null}
        onRetry={() => {}}
        instanceHref={() => ""}
        selectedWorkflowId={null}
        onCloseInstance={() => {}}
        detail={null}
      />
    );
  }
  render(<EmptyPage />);
  expect(screen.getByRole("button", { name: "Older" })).toBeEnabled();
  expect(screen.queryByRole("textbox")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Older" }));
  expect(screen.getByRole("button", { name: "Newer" })).toBeEnabled();
});
it("does not fall back to latest for legacy links missing the run ID", () => {
  const api = transport(() => {
    throw new Error("unexpected query");
  });
  const hook = renderHook(() => useWorkflowInstanceDetailPanel("reusable", null), {
    wrapper: api.wrapper,
  });
  expect(hook.result.current.error?.message).toContain("no execution ID");
  expect(api.requests).toEqual([]);
  api.client.stop();
});
it.each(["cancel", "terminate"] as const)(
  "binds %s to the reviewed run and handles a lost reply",
  async (operation) => {
    const api = transport(() => new Error("private failure body"));
    const after = vi.fn();
    const prompt = vi.spyOn(window, "prompt").mockReturnValue("reviewed reason");
    const hook = renderHook(() => useInstanceAdminControls("reused-id", "reviewed-run", after), {
      wrapper: api.wrapper,
    });
    let pending: Promise<void>;
    act(() => {
      pending =
        operation === "cancel" ? hook.result.current.onCancel() : hook.result.current.onTerminate();
    });
    fireEvent.click(
      await screen.findByRole("button", {
        name: operation === "cancel" ? "Cancel workflow" : "Terminate",
      })
    );
    await act(async () => {
      await pending;
    });
    expect(api.requests[0].variables).toMatchObject({
      workflowId: "reused-id",
      runId: "reviewed-run",
    });
    expect(toast.error).toHaveBeenCalledWith(expect.stringContaining("Refresh this execution"));
    expect(JSON.stringify(vi.mocked(toast.error).mock.calls)).not.toContain("private failure body");
    expect(after).not.toHaveBeenCalled();
    prompt.mockRestore();
    api.client.stop();
  }
);
it("handles GraphQL permission errors without acknowledging a write or exposing its response", async () => {
  const api = transport(() => ({
    errors: [{ message: "private denial", extensions: { code: "PERMISSION_DENIED" } }],
  }));
  const after = vi.fn();
  const hook = renderHook(() => useInstanceAdminControls("id", "run", after), {
    wrapper: api.wrapper,
  });
  let pending: Promise<void>;
  act(() => {
    pending = hook.result.current.onCancel();
  });
  fireEvent.click(await screen.findByRole("button", { name: "Cancel workflow" }));
  await act(async () => {
    await pending;
  });
  expect(toast.error).toHaveBeenCalledWith(expect.stringContaining("permissions"));
  expect(toast.success).not.toHaveBeenCalled();
  expect(after).not.toHaveBeenCalled();
  api.client.stop();
});

it("abandons an open confirmation after selecting a different execution", async () => {
  const api = transport(() => ({ data: { cancelWorkflowInstance: { ok: true, errors: [] } } }));
  let run = "original";
  const hook = renderHook(() => useInstanceAdminControls("same-id", run, vi.fn()), {
    wrapper: api.wrapper,
  });
  let pending: Promise<void>;
  act(() => {
    pending = hook.result.current.onCancel();
  });
  await screen.findByRole("button", { name: "Cancel workflow" });
  run = "replacement";
  hook.rerender();
  fireEvent.click(screen.getByRole("button", { name: "Cancel workflow" }));
  await act(async () => {
    await pending;
  });
  expect(api.requests).toEqual([]);
  api.client.stop();
});
