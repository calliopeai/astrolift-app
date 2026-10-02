import { ApolloClient, ApolloLink, InMemoryCache, type Operation } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, render, renderHook, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { Observable, type Subscriber } from "rxjs";
import { afterEach, expect, it, vi } from "vitest";
import { PipelineDetailScreen } from "./PipelineDetail";
import { DETAIL } from "./pipelines-previews.fixtures";
import { usePipelineDetail } from "./use-pipeline-detail";
import messages from "@/messages/en.json";

const navigation = vi.hoisted(() => ({
  pathname: "/pipelines/pipeline/secrets",
  params: new URLSearchParams(),
}));
vi.mock("next/navigation", () => ({
  usePathname: () => navigation.pathname,
  useSearchParams: () => navigation.params,
  useRouter: () => ({ replace: vi.fn() }),
}));
vi.mock("@/components/PageShell", () => ({
  PageShell: ({ title, children }: { title: ReactNode; children: ReactNode }) => (
    <div>
      <h1>{title}</h1>
      {children}
    </div>
  ),
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

afterEach(() => {
  navigation.params = new URLSearchParams();
});

it("reads the actual pipeline name on the secrets route without requesting runs", async () => {
  const requests: Operation[] = [];
  const transport = network((operation, observer) => {
    requests.push(operation);
    complete(observer, { astroliftPipeline: DETAIL.pipeline });
  });
  const hook = renderHook(() => usePipelineDetail("pipeline-guid"), { wrapper: transport.wrapper });
  await waitFor(() => expect(hook.result.current.pipeline?.name).toBe("Build and release"));
  expect(hook.result.current.tab).toBe("secrets");
  expect(requests.map((operation) => operation.operationName)).toEqual(["GetPipeline"]);
  expect(requests[0].variables).toEqual({ id: "pipeline-guid" });
  render(
    <PipelineDetailScreen
      {...hook.result.current}
      pipelineId="pipeline-guid"
      secrets={<div>Secret bindings</div>}
    />,
    { wrapper: transport.wrapper }
  );
  expect(screen.getByRole("heading", { name: "Build and release" })).toBeVisible();
  expect(screen.getByText("Secret bindings")).toBeVisible();
  expect(screen.queryByText("No runs yet")).not.toBeInTheDocument();
  hook.unmount();
  transport.client.stop();
});

it("distinguishes pipeline metadata refusal from a missing definition and retries the read", async () => {
  let fail = true;
  let count = 0;
  const transport = network((_operation, observer) => {
    count++;
    if (fail) observer.error(new Error("Permission denied"));
    else complete(observer, { astroliftPipeline: null });
  });
  const hook = renderHook(() => usePipelineDetail("pipeline-guid"), { wrapper: transport.wrapper });
  await waitFor(() => expect(hook.result.current.pipelineError?.message).toBe("Permission denied"));
  const view = render(
    <PipelineDetailScreen {...hook.result.current} secrets={<div>Secret bindings</div>} />,
    { wrapper: transport.wrapper }
  );
  expect(screen.getByRole("alert")).toHaveTextContent("Could not load pipeline");
  expect(screen.queryByText("Pipeline definition unavailable.")).not.toBeInTheDocument();
  fail = false;
  act(() => hook.result.current.onRetryPipeline());
  await waitFor(() => expect(hook.result.current.pipelineError).toBeNull());
  expect(count).toBe(2);
  view.rerender(
    <PipelineDetailScreen {...hook.result.current} secrets={<div>Secret bindings</div>} />
  );
  expect(screen.getByText("Pipeline definition unavailable.")).toBeVisible();
  hook.unmount();
  transport.client.stop();
});
