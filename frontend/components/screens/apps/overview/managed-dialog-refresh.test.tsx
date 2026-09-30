import { ApolloClient, ApolloLink, InMemoryCache, Observable } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import en from "@/messages/en.json";
import { DEPTH, OBJECT_STORE, OBJECTS, QUEUE } from "./app-overview-cards-b.fixtures";
import { ListObjectsDialogView, QueueDepthDialogView } from "./ManagedServicesSummaryCard";
import {
  useManagedServiceObjects,
  useQueueDepth,
  type SummaryService,
} from "./use-managed-services-summary";
const cases = [
  {
    kind: "objects",
    svc: OBJECT_STORE,
    operation: "ListManagedServiceObjects",
    field: "astroliftManagedServiceObjects",
    snapshot: OBJECTS,
    display: OBJECTS.objects[0].key,
    updated: { ...OBJECTS, objects: [{ ...OBJECTS.objects[0], key: "new-actual-object-key" }] },
    newDisplay: "new-actual-object-key",
    empty: en.apps.settings.managedServicesSummary.objectsDialog.empty,
  },
  {
    kind: "queue",
    svc: QUEUE,
    operation: "GetManagedServiceQueueDepth",
    field: "astroliftManagedServiceQueueDepth",
    snapshot: DEPTH,
    display: "1,284",
    updated: { ...DEPTH, depth: 2876 },
    newDisplay: "2,876",
    empty: en.apps.settings.managedServicesSummary.depthDialog.noSnapshot,
  },
] as const;
interface PendingRead {
  operation: string;
  variables: Record<string, unknown>;
  succeed: (data: Record<string, unknown>) => void;
  fail: (message: string) => void;
}
function transport() {
  const reads: PendingRead[] = [];
  const link = new ApolloLink(
    (operation) =>
      new Observable((observer) => {
        reads.push({
          operation: operation.operationName ?? "",
          variables: operation.variables,
          succeed: (data) => {
            observer.next({ data });
            observer.complete();
          },
          fail: (message) => observer.error(new Error(message)),
        });
      })
  );
  return { reads, client: new ApolloClient({ link, cache: new InMemoryCache() }) };
}
function Objects({ svc }: { svc: SummaryService }) {
  const [open, setOpen] = useState(true);
  return (
    <ListObjectsDialogView
      svc={svc}
      open={open}
      onOpenChange={setOpen}
      {...useManagedServiceObjects(svc, open)}
    />
  );
}
function Queue({ svc }: { svc: SummaryService }) {
  const [open, setOpen] = useState(true);
  return (
    <QueueDepthDialogView
      svc={svc}
      open={open}
      onOpenChange={setOpen}
      {...useQueueDepth(svc, open)}
    />
  );
}
describe.each(cases)("$kind snapshot dialog", (entry) => {
  function frame(client: ApolloClient, svc: SummaryService = entry.svc) {
    return (
      <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
        <ApolloProvider client={client}>
          {entry.kind === "objects" ? <Objects svc={svc} /> : <Queue svc={svc} />}
        </ApolloProvider>
      </NextIntlClientProvider>
    );
  }
  it("retains open snapshot through pending/failure, prevents duplicate reads and explicitly retries the exact selected service", async () => {
    const { client, reads } = transport();
    render(frame(client));
    await waitFor(() => expect(reads).toHaveLength(1));
    const variables =
      entry.kind === "objects"
        ? { managedServiceId: entry.svc.id, limit: 10 }
        : { managedServiceId: entry.svc.id };
    expect(reads[0]).toMatchObject({ operation: entry.operation, variables });
    await act(async () => reads[0].succeed({ [entry.field]: entry.snapshot }));
    expect(screen.getByText(entry.display)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(reads).toHaveLength(2));
    expect(screen.getByRole("alertdialog")).toHaveAttribute("aria-busy", "true");
    expect(screen.getByText(entry.display)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Refresh" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(reads).toHaveLength(2);
    expect(reads[1]).toMatchObject({ operation: entry.operation, variables });
    await act(async () => reads[1].fail("actual upstream snapshot read failed"));
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("actual upstream snapshot read failed")
    );
    expect(screen.getByRole("alertdialog")).toHaveAttribute("aria-busy", "false");
    expect(screen.getByText(entry.display)).toBeInTheDocument();
    expect(screen.queryByText(entry.empty)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Refresh" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(reads).toHaveLength(3));
    expect(reads[2]).toMatchObject({ operation: entry.operation, variables });
    await act(async () => reads[2].succeed({ [entry.field]: entry.updated }));
    await waitFor(() => expect(screen.getByText(entry.newDisplay)).toBeInTheDocument());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(reads).toHaveLength(3);
  });
  it("presents an initial read failure without an empty or loading claim and allows a real retry", async () => {
    const { client, reads } = transport();
    render(frame(client));
    await waitFor(() => expect(reads).toHaveLength(1));
    await act(async () => reads[0].fail("actual initial read denied"));
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("actual initial read denied")
    );
    expect(screen.queryByText(entry.empty)).not.toBeInTheDocument();
    expect(within(screen.getByRole("alertdialog")).queryByText("Loading…")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(reads).toHaveLength(2));
    await act(async () => reads[1].succeed({ [entry.field]: entry.snapshot }));
    await waitFor(() => expect(screen.getByText(entry.display)).toBeInTheDocument());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
  it("does not reuse the prior service snapshot when the selected service changes", async () => {
    const { client, reads } = transport();
    const view = render(frame(client));
    await waitFor(() => expect(reads).toHaveLength(1));
    await act(async () => reads[0].succeed({ [entry.field]: entry.snapshot }));
    expect(screen.getByText(entry.display)).toBeInTheDocument();
    const next = { ...entry.svc, id: "another-service-id", name: "Another selected service" };
    view.rerender(frame(client, next));
    await waitFor(() => expect(reads).toHaveLength(2));
    expect(screen.queryByText(entry.display)).not.toBeInTheDocument();
    expect(reads[1].variables.managedServiceId).toBe(next.id);
    await act(async () => reads[1].fail("next selected service denied"));
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("next selected service denied")
    );
    expect(screen.queryByText(entry.display)).not.toBeInTheDocument();
  });
});
