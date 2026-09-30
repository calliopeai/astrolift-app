import { ApolloClient, ApolloLink, InMemoryCache, type Operation } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import {
  act,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { Observable, type Subscriber } from "rxjs";
import { expect, it, vi } from "vitest";

import messages from "@/messages/en.json";
import { PermissionsProvider } from "@/providers/PermissionsProvider";
import { DangerZoneView } from "./DangerZone";
import { DEREGISTER_PREVIEW } from "./app-settings-members.fixtures";
import { useDangerZone } from "./use-danger-zone";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

type Observer = Subscriber<{ data: Record<string, unknown> }>;
function network(
  reply: (operation: Operation, observer: Observer) => void,
  grants: string[] = ["app.delete"],
  loading = false
) {
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new ApolloLink((operation) => new Observable((observer) => reply(operation, observer))),
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
      <ApolloProvider client={client}>
        <PermissionsProvider value={{ granted: new Set(grants), loading }}>
          {children}
        </PermissionsProvider>
      </ApolloProvider>
    </NextIntlClientProvider>
  );
  return { client, wrapper };
}

function complete(observer: Observer) {
  observer.next({ data: { previewAstroliftDeregister: DEREGISTER_PREVIEW } });
  observer.complete();
}

function LiveDangerZone() {
  return <DangerZoneView {...useDangerZone("checkout", "Checkout")} />;
}

it("loads the actual resource count before opening the confirmation", async () => {
  const requests: Operation[] = [];
  const transport = network((operation, observer) => {
    requests.push(operation);
    complete(observer);
  });
  const view = render(<LiveDangerZone />, { wrapper: transport.wrapper });
  const trigger = screen.getByRole("button", { name: /Deregister app/ });
  await waitFor(() =>
    expect(within(trigger).getByText(String(DEREGISTER_PREVIEW.totalResourceCount))).toBeVisible()
  );
  expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  expect(requests).toHaveLength(1);
  expect(requests[0].operationName).toBe("PreviewDeregisterApp");
  expect(requests[0].variables).toEqual({ appSlug: "checkout" });
  fireEvent.click(trigger);
  await waitFor(() => expect(requests).toHaveLength(2));
  expect(requests.every((request) => request.operationName === "PreviewDeregisterApp")).toBe(true);
  const dialog = screen.getByRole("alertdialog");
  fireEvent.change(within(dialog).getByRole("textbox"), { target: { value: "Checkout" } });
  expect(within(dialog).getByRole("button", { name: "Deregister app" })).toBeEnabled();
  view.unmount();
  transport.client.stop();
});

it.each([
  { grants: [], loading: false },
  { grants: ["app.delete"], loading: true },
])(
  "does not preload when delete admission is absent or unknown: %j",
  async ({ grants, loading }) => {
    const requests: Operation[] = [];
    const transport = network(
      (operation, observer) => {
        requests.push(operation);
        complete(observer);
      },
      grants,
      loading
    );
    const hook = renderHook(() => useDangerZone("checkout", "Checkout"), {
      wrapper: transport.wrapper,
    });
    await act(async () => hook.result.current.loadPreview());
    expect(requests).toEqual([]);
    expect(hook.result.current.preview).toBeNull();
    hook.unmount();
    transport.client.stop();
  }
);

it("drops a stale count after refresh refusal and blocks confirmation", async () => {
  let requests = 0;
  const transport = network((_operation, observer) => {
    requests += 1;
    if (requests === 1) complete(observer);
    else observer.error(new Error("Current credential cannot preview this app"));
  });
  const view = render(<LiveDangerZone />, { wrapper: transport.wrapper });
  const trigger = screen.getByRole("button", { name: /Deregister app/ });
  await waitFor(() =>
    expect(within(trigger).getByText(String(DEREGISTER_PREVIEW.totalResourceCount))).toBeVisible()
  );
  fireEvent.click(trigger);
  await waitFor(() =>
    expect(
      within(trigger).queryByText(String(DEREGISTER_PREVIEW.totalResourceCount))
    ).not.toBeInTheDocument()
  );
  const dialog = screen.getByRole("alertdialog");
  expect(within(dialog).getByText("Resource preview unavailable.")).toBeVisible();
  expect(within(dialog).queryByText("No resources will be destroyed.")).not.toBeInTheDocument();
  fireEvent.change(within(dialog).getByRole("textbox"), { target: { value: "Checkout" } });
  expect(within(dialog).getByRole("button", { name: "Deregister app" })).toBeDisabled();
  expect(requests).toBe(2);
  view.unmount();
  transport.client.stop();
});
