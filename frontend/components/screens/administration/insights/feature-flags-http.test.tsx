import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import en from "@/messages/en.json";
import { GET_ME } from "@/graphql/user/user.queries";
import { SERVER_INFO } from "@/graphql/server/server.queries";
import { FeaturesScreen } from "./FeaturesScreen";
import { useFeatureFlags } from "./use-feature-flags";

const flag = { key: "models.bedrock_connections_enabled", enabled: false, description: "Models" };
const viewer = (id = "viewer-a") => ({
  id,
  profile: { id: `${id}-profile`, username: id },
  modules: [],
});
const inventory = (enabled = false) => ({
  data: { astroliftServerInfo: { featureFlags: [{ ...flag, enabled }], buildTimeFeatures: [] } },
});
const accepted = (data = { ...flag, enabled: true }) => ({
  data: { setFeatureFlag: { ok: true, errors: [], data } },
});
const deferred = <T,>() => {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
};
function Harness() {
  const flags = useFeatureFlags();
  const [outcome, setOutcome] = useState("");
  return (
    <>
      <button
        disabled={!flags.runtimeFlags.length || flags.controlsDisabled}
        onClick={() => {
          void flags.toggleFlag(flags.runtimeFlags[0]).then(
            () => setOutcome("accepted"),
            () => setOutcome("failed")
          );
        }}
      >
        Enable models
      </button>
      <button onClick={flags.onRetry}>Read again</button>
      <output aria-label="write outcome">{outcome}</output>
      <output aria-label="recovery">{flags.recovery?.kind ?? "none"}</output>
      <output aria-label="current value">{String(flags.runtimeFlags[0]?.enabled)}</output>
      <output aria-label="read error">{flags.error?.message ?? ""}</output>
    </>
  );
}
function ConnectedScreen() {
  return <FeaturesScreen {...useFeatureFlags()} />;
}
function mount(
  handler: (
    operation: string,
    body: { variables?: { key: string; enabled: boolean } }
  ) => unknown | Promise<unknown>,
  full = false
) {
  const writes: unknown[] = [],
    operations: string[] = [];
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new HttpLink({
      uri: "/fixture/graphql",
      fetch: async (_url, init) => {
        const body = JSON.parse(String(init?.body));
        operations.push(body.operationName);
        if (body.operationName === "SetFeatureFlag") writes.push(body.variables);
        const value = await handler(body.operationName, body);
        return new Response(JSON.stringify(value), {
          headers: { "Content-Type": "application/json" },
        });
      },
    }),
  });
  client.writeQuery({ query: GET_ME, data: { me: viewer() } });
  const rendered = render(
    <ApolloProvider client={client}>
      <NextIntlClientProvider locale="en" messages={en}>
        {full ? <ConnectedScreen /> : <Harness />}
      </NextIntlClientProvider>
    </ApolloProvider>
  );
  return { client, writes, operations, ...rendered };
}
const click = async () => {
  await waitFor(() => expect(screen.getByRole("button", { name: "Enable models" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Enable models" }));
};
const normalRead = (op: string, enabled = false) =>
  op === "Me" ? { data: { me: viewer() } } : inventory(enabled);

describe("feature flag write and read outcomes over actual HttpLink", () => {
  it("keeps exact accepted state and closes confirmation when inventory refresh fails", async () => {
    let written = false;
    const h = mount(
      (op) =>
        op === "SetFeatureFlag"
          ? ((written = true), accepted())
          : written && op === "AdminFeatureInventory"
            ? { errors: [{ message: "Read unavailable" }] }
            : normalRead(op),
      true
    );
    await waitFor(() =>
      expect(screen.getByRole("switch", { name: `Toggle ${flag.key}` })).toBeEnabled()
    );
    fireEvent.click(screen.getByRole("switch"));
    fireEvent.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Enable flag" })
    );
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("was accepted"));
    expect(screen.getByRole("switch")).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("switch")).toBeDisabled();
    expect(h.writes).toEqual([{ key: flag.key, enabled: true }]);
    expect(h.operations).toContain("AstroliftServerInfo");
    expect(h.operations).toContain("Me");
    h.client.stop();
  });

  it("recovers failed navigation reads without resending and updates navigation and viewer caches", async () => {
    let written = false,
      fail = true;
    const h = mount((op) =>
      op === "SetFeatureFlag"
        ? ((written = true), accepted())
        : written && op === "AstroliftServerInfo" && fail
          ? { errors: [{ message: "Navigation unavailable" }] }
          : normalRead(op, written)
    );
    await click();
    await waitFor(() => expect(screen.getByLabelText("read error")).not.toHaveTextContent(/^$/));
    expect(screen.getByLabelText("recovery")).toHaveTextContent("accepted");
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Read again" }));
    await waitFor(() => expect(screen.getByLabelText("recovery")).toHaveTextContent("none"));
    expect(screen.getByRole("button", { name: "Enable models" })).toBeEnabled();
    expect(h.client.readQuery({ query: SERVER_INFO })).toMatchObject({
      astroliftServerInfo: { featureFlags: [{ key: flag.key, enabled: true }] },
    });
    expect(h.writes).toHaveLength(1);
    h.client.stop();
  });

  it.each(["lost", "wrong key", "wrong value", "missing data"])(
    "blocks duplicate %s write until a fresh authoritative read",
    async (kind) => {
      let written = false;
      const h = mount((op) => {
        if (op !== "SetFeatureFlag") return normalRead(op, written);
        written = true;
        if (kind === "lost") throw new Error("Transport reply lost");
        if (kind === "missing data")
          return { data: { setFeatureFlag: { ok: true, errors: [], data: null } } };
        return accepted({
          ...flag,
          key: kind === "wrong key" ? "other.flag" : flag.key,
          enabled: kind !== "wrong value",
        });
      });
      await click();
      await waitFor(() => expect(screen.getByLabelText("recovery")).toHaveTextContent("uncertain"));
      expect(screen.getByRole("button", { name: "Enable models" })).toBeDisabled();
      expect(h.operations.filter((op) => op === "AdminFeatureInventory")).toHaveLength(1);
      fireEvent.click(screen.getByRole("button", { name: "Read again" }));
      await waitFor(() => expect(screen.getByLabelText("recovery")).toHaveTextContent("none"));
      expect(screen.getByLabelText("current value")).toHaveTextContent("true");
      expect(h.writes).toHaveLength(1);
      h.client.stop();
    }
  );

  it("keeps explicit server rejection actionable without acceptance or refresh", async () => {
    const h = mount((op) =>
      op === "SetFeatureFlag"
        ? {
            data: {
              setFeatureFlag: {
                ok: false,
                errors: [
                  { code: "PERMISSION_DENIED", message: "Platform operator required", field: null },
                ],
                data: null,
              },
            },
          }
        : normalRead(op)
    );
    await click();
    await waitFor(() => expect(screen.getByLabelText("write outcome")).toHaveTextContent("failed"));
    expect(screen.getByLabelText("recovery")).toHaveTextContent("none");
    expect(screen.getByRole("button", { name: "Enable models" })).toBeEnabled();
    expect(h.operations.filter((op) => op === "AdminFeatureInventory")).toHaveLength(1);
    h.client.stop();
  });

  it("synchronously prevents two clicks from sending two mutations", async () => {
    const held = deferred<unknown>();
    const h = mount((op) => (op === "SetFeatureFlag" ? held.promise : normalRead(op)));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Enable models" })).toBeEnabled()
    );
    const button = screen.getByRole("button", { name: "Enable models" });
    fireEvent.click(button);
    fireEvent.click(button);
    await waitFor(() => expect(h.writes).toHaveLength(1));
    await act(async () => held.resolve(accepted()));
    await waitFor(() => expect(screen.getByLabelText("current value")).toHaveTextContent("false"));
    expect(h.writes).toHaveLength(1);
    h.client.stop();
  });

  it("refuses stale viewer acknowledgments even after an actor ABA switch", async () => {
    const held = deferred<unknown>();
    const h = mount((op) => (op === "SetFeatureFlag" ? held.promise : normalRead(op)));
    await click();
    await act(async () => h.client.writeQuery({ query: GET_ME, data: { me: viewer("viewer-b") } }));
    await act(async () => h.client.writeQuery({ query: GET_ME, data: { me: viewer() } }));
    await act(async () => held.resolve(accepted()));
    await waitFor(() => expect(screen.getByLabelText("write outcome")).toHaveTextContent("failed"));
    expect(screen.getByLabelText("recovery")).toHaveTextContent("none");
    expect(screen.getByLabelText("current value")).toHaveTextContent("false");
    expect(h.operations).not.toContain("AstroliftServerInfo");
    h.client.stop();
  });

  it("does not restore accepted state or navigation after unmount", async () => {
    const held = deferred<unknown>();
    const h = mount((op) => (op === "SetFeatureFlag" ? held.promise : normalRead(op)));
    await click();
    h.unmount();
    await act(async () => held.resolve(accepted()));
    expect(h.operations).not.toContain("AstroliftServerInfo");
    h.client.stop();
  });

  it("keeps recovery blocked when the fresh inventory is malformed", async () => {
    let written = false;
    const h = mount((op) =>
      op === "SetFeatureFlag"
        ? ((written = true), { data: { setFeatureFlag: { ok: true, errors: [], data: null } } })
        : written && op === "AdminFeatureInventory"
          ? {
              data: {
                astroliftServerInfo: {
                  featureFlags: [{ ...flag, enabled: null }],
                  buildTimeFeatures: [],
                },
              },
            }
          : normalRead(op)
    );
    await click();
    await waitFor(() => expect(screen.getByLabelText("recovery")).toHaveTextContent("uncertain"));
    fireEvent.click(screen.getByRole("button", { name: "Read again" }));
    await waitFor(() => expect(screen.getByLabelText("read error")).not.toHaveTextContent(/^$/));
    expect(screen.getByLabelText("recovery")).toHaveTextContent("uncertain");
    h.client.stop();
  });
  it("does not release late navigation or inventory reads after viewer ABA", async () => {
    const held = deferred<unknown>();
    let written = false;
    const h = mount((op) =>
      op === "SetFeatureFlag"
        ? ((written = true), accepted())
        : written && op === "AstroliftServerInfo"
          ? held.promise
          : normalRead(op, written)
    );
    await click();
    await waitFor(() => expect(h.operations).toContain("AstroliftServerInfo"));
    await act(async () => h.client.writeQuery({ query: GET_ME, data: { me: viewer("viewer-b") } }));
    written = false;
    await act(async () => h.client.writeQuery({ query: GET_ME, data: { me: viewer() } }));
    await waitFor(() => expect(screen.getByLabelText("current value")).toHaveTextContent("false"));
    await act(async () => held.resolve(inventory(true)));
    expect(screen.getByLabelText("current value")).toHaveTextContent("false");
    expect(h.client.readQuery({ query: SERVER_INFO })).toBeNull();
    expect(screen.getByRole("button", { name: "Enable models" })).toBeEnabled();
    h.client.stop();
  });

  it("blocks further toggles when fresh viewer admission returns no current user", async () => {
    const h = mount((op) =>
      op === "SetFeatureFlag" ? accepted() : op === "Me" ? { data: { me: null } } : normalRead(op)
    );
    await click();
    await waitFor(() =>
      expect(screen.getByLabelText("read error")).toHaveTextContent("viewer changed")
    );
    expect(screen.getByLabelText("recovery")).toHaveTextContent("none");
    expect(screen.getByRole("button", { name: "Enable models" })).toBeDisabled();
    expect(h.client.readQuery({ query: GET_ME })).toEqual({ me: null });
    expect(h.client.readQuery({ query: SERVER_INFO })).toBeNull();
    expect(h.writes).toHaveLength(1);
    h.client.stop();
  });

  it("keeps an uncertain write blocked when its key disappears from the fresh inventory", async () => {
    let written = false;
    const h = mount((op) =>
      op === "SetFeatureFlag"
        ? ((written = true), { data: { setFeatureFlag: { ok: true, errors: [], data: null } } })
        : written && op === "AdminFeatureInventory"
          ? { data: { astroliftServerInfo: { featureFlags: [], buildTimeFeatures: [] } } }
          : normalRead(op)
    );
    await click();
    await waitFor(() => expect(screen.getByLabelText("recovery")).toHaveTextContent("uncertain"));
    fireEvent.click(screen.getByRole("button", { name: "Read again" }));
    await waitFor(() =>
      expect(screen.getByLabelText("read error")).toHaveTextContent("Could not refresh")
    );
    expect(screen.getByLabelText("recovery")).toHaveTextContent("uncertain");
    expect(h.writes).toHaveLength(1);
    h.client.stop();
  });
  it("does not project the previous viewer's accepted receipt during a held new inventory read", async () => {
    const nav = deferred<unknown>(),
      nextInventory = deferred<unknown>();
    let written = false,
      switched = false;
    const h = mount((op) =>
      op === "SetFeatureFlag"
        ? ((written = true), accepted())
        : switched && op === "AdminFeatureInventory"
          ? nextInventory.promise
          : written && op === "AstroliftServerInfo"
            ? nav.promise
            : normalRead(op)
    );
    await click();
    await waitFor(() => expect(screen.getByLabelText("recovery")).toHaveTextContent("accepted"));
    switched = true;
    await act(async () => h.client.writeQuery({ query: GET_ME, data: { me: viewer("viewer-b") } }));
    expect(screen.getByLabelText("recovery")).toHaveTextContent("none");
    expect(screen.getByLabelText("current value")).toHaveTextContent("undefined");
    expect(screen.getByRole("button", { name: "Enable models" })).toBeDisabled();
    await act(async () => {
      nextInventory.resolve(inventory(false));
      nav.resolve(inventory(true));
    });
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Enable models" })).toBeEnabled()
    );
    expect(screen.getByLabelText("recovery")).toHaveTextContent("none");
    expect(h.client.readQuery({ query: SERVER_INFO })).toBeNull();
    h.client.stop();
  });
});
