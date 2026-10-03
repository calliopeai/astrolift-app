import { readFileSync } from "node:fs";
import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { buildSchema, parse, validate } from "graphql";
import { NextIntlClientProvider } from "next-intl";
import { useState, type PropsWithChildren } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import en from "@/messages/en.json";
import { ModelHostingSourcePanel } from "./ModelHostingSourcePanel";
import { useModelHostingSource } from "./use-model-hosting-source";
const identity = vi.hoisted(() => ({
  org: "00000000-0000-4000-8000-000000000001",
  actor: "actor-one",
}));
vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({ org: { id: identity.org }, loading: false, error: null }),
}));
vi.mock("@/graphql/user/user.hooks", () => ({
  useMe: () => ({ user: { id: identity.actor }, loading: false, error: null }),
}));
type Request = { operationName: string; query: string; variables: Record<string, unknown> };
let requests: Request[], transport: (r: Request) => Promise<Response>;
const schema = buildSchema(readFileSync("../backend/schema.graphql", "utf8"));
const connection = {
  id: "00000000-0000-4000-8000-000000000002",
  version: 3,
  name: "private access",
  accountUsername: "hf-account",
  verifiedAt: "2026-10-03T12:00:00Z",
};
function response(data: Record<string, unknown>) {
  return new Response(JSON.stringify({ data }), {
    headers: { "Content-Type": "application/json" },
  });
}
function fixture(r: Request) {
  switch (r.operationName) {
    case "GetModelHostingAction":
      return response({ modelHostingAction: { allowed: true, reason: null } });
    case "ListHuggingFaceConnections":
      return response({
        huggingFaceConnectionsPage: {
          items: [connection],
          totalCount: 51,
          page: r.variables.page,
          pageSize: 25,
        },
      });
    case "ConnectHuggingFace":
      return response({ connectHuggingFace: { ok: true, data: connection, errors: [] } });
    case "GetModelSourceAccess":
      return response({
        clusterModelSourceAccess: {
          accessible: true,
          reason: null,
          observedAt: "2026-10-03T12:00:00Z",
          model: {
            repoId: r.variables.modelRepo,
            revisionSha: r.variables.revisionSha,
            license: "literal-license",
            gated: "MANUAL",
            architectures: [],
            pipelineTag: "text-generation",
            library: "transformers",
            author: "owner",
            downloads: null,
            likes: null,
            compatibility: "UNKNOWN",
          },
        },
      });
    default:
      throw new Error(`Unexpected ${r.operationName}`);
  }
}
function wrapper() {
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    devtools: { enabled: false },
    link: new HttpLink({
      uri: "http://product.test/gql",
      fetch: async (_uri, options) => {
        const r = JSON.parse(String(options?.body));
        expect(validate(schema, parse(r.query))).toEqual([]);
        requests.push(r);
        return transport(r);
      },
    }),
  });
  return function Provider({ children }: PropsWithChildren) {
    return (
      <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
        <ApolloProvider client={client}>{children}</ApolloProvider>
      </NextIntlClientProvider>
    );
  };
}
function Context() {
  const [model, setModel] = useState<{ repoId: string; revisionSha: string } | null>(null);
  const h = useModelHostingSource(model, setModel);
  return (
    <>
      <ModelHostingSourcePanel {...h.props} />
      <output aria-label="source-access">{h.access.confirmed ? "confirmed" : "unknown"}</output>
    </>
  );
}
function Frame() {
  return <Context key={`${identity.org}:${identity.actor}`} />;
}
beforeEach(() => {
  identity.org = "00000000-0000-4000-8000-000000000001";
  identity.actor = "actor-one";
  requests = [];
  transport = async (r) => fixture(r);
});
async function connect() {
  const t = en.models.shared.hosting;
  await waitFor(() => expect(screen.getByLabelText(t.connection)).toBeEnabled());
  fireEvent.click(screen.getByText(t.connect, { selector: "summary" }));
  fireEvent.change(screen.getByLabelText(t.connectionName), {
    target: { value: "private access" },
  });
  fireEvent.change(screen.getByLabelText(t.token), { target: { value: "hf_test_product_secret" } });
  fireEvent.click(screen.getByRole("button", { name: t.connect }));
}
const reads = () => requests.filter((r) => r.operationName === "ListHuggingFaceConnections");
describe("actual schema-validated HF connection HttpLink", () => {
  it("saves write-only credential, then warns separately when exact inventory refresh fails", async () => {
    transport = async (r) =>
      r.operationName === "ListHuggingFaceConnections" && reads().length > 1
        ? Promise.reject(new Error("inventory read failed"))
        : fixture(r);
    render(<Frame />, { wrapper: wrapper() });
    await connect();
    expect(
      await screen.findByText(en.models.shared.hosting.connected.replace("{account}", "hf-account"))
    ).toBeInTheDocument();
    expect(
      await screen.findByText(en.models.shared.hosting.savedRefreshFailed)
    ).toBeInTheDocument();
    expect(screen.getByLabelText(en.models.shared.hosting.token)).toHaveValue("");
    expect(requests.find((r) => r.operationName === "ConnectHuggingFace")?.variables.input).toEqual(
      { organizationId: identity.org, name: "private access", token: "hf_test_product_secret" }
    );
  });
  it("keeps refusal literal, draft intact and makes no refresh request", async () => {
    transport = async (r) =>
      r.operationName === "ConnectHuggingFace"
        ? response({
            connectHuggingFace: {
              ok: false,
              data: null,
              errors: [
                {
                  code: "PERMISSION_DENIED",
                  message: "Administrative grant removed",
                  field: null,
                  currentVersion: null,
                  requestedVersion: null,
                  requiresAttestation: false,
                  supportedMethods: [],
                },
              ],
            },
          })
        : fixture(r);
    render(<Frame />, { wrapper: wrapper() });
    await connect();
    expect(await screen.findByText("Administrative grant removed")).toBeInTheDocument();
    expect(reads()).toHaveLength(1);
    expect(screen.getByLabelText(en.models.shared.hosting.token)).toHaveValue(
      "hf_test_product_secret"
    );
  });
  it("distinguishes lost response from accepted or definitely rejected connection", async () => {
    transport = async (r) =>
      r.operationName === "ConnectHuggingFace"
        ? Promise.reject(new Error("transport unavailable"))
        : fixture(r);
    render(<Frame />, { wrapper: wrapper() });
    await connect();
    expect(await screen.findByText(en.models.shared.hosting.connectFailed)).toBeInTheDocument();
    expect(reads()).toHaveLength(1);
  });
  it("does not read credentials/access when actual server denies hosting", async () => {
    transport = async (r) =>
      r.operationName === "GetModelHostingAction"
        ? response({
            modelHostingAction: { allowed: false, reason: "Administrative scope required" },
          })
        : fixture(r);
    render(<Frame />, { wrapper: wrapper() });
    expect(await screen.findByText("Administrative scope required")).toBeInTheDocument();
    expect(reads()).toHaveLength(0);
    expect(screen.getByLabelText(en.models.shared.hosting.token)).toBeDisabled();
  });
  it("pages on server and binds private source access to exact connection version", async () => {
    render(<Frame />, { wrapper: wrapper() });
    await waitFor(() =>
      expect(screen.getByLabelText(en.models.shared.hosting.connection)).toBeEnabled()
    );
    fireEvent.click(screen.getByRole("button", { name: en.models.shared.hosting.older }));
    await waitFor(() => expect(reads().at(-1)?.variables).toMatchObject({ page: 2, pageSize: 25 }));
    await waitFor(() =>
      expect(screen.getByLabelText(en.models.shared.hosting.connection)).toBeEnabled()
    );
    fireEvent.change(screen.getByLabelText(en.models.shared.hosting.connection), {
      target: { value: connection.id },
    });
    fireEvent.click(
      screen.getByText(en.models.shared.hosting.manualTitle, { selector: "summary" })
    );
    fireEvent.change(screen.getByLabelText(en.models.shared.hosting.repository), {
      target: { value: "owner/private" },
    });
    fireEvent.change(screen.getByLabelText(en.models.shared.hosting.revision), {
      target: { value: "a".repeat(40) },
    });
    fireEvent.click(screen.getByRole("button", { name: en.models.shared.hosting.useSource }));
    await waitFor(() =>
      expect(screen.getByLabelText("source-access")).toHaveTextContent("confirmed")
    );
    expect(requests.find((r) => r.operationName === "GetModelSourceAccess")?.variables).toEqual({
      organizationId: identity.org,
      modelRepo: "owner/private",
      revisionSha: "a".repeat(40),
      connectionId: connection.id,
      expectedConnectionVersion: 3,
    });
  });
  it("ignores accepted old organization reply and never refreshes new organization from it", async () => {
    let release!: (r: Response) => void;
    transport = async (r) =>
      r.operationName === "ConnectHuggingFace"
        ? new Promise((done) => {
            release = done;
          })
        : fixture(r);
    const { rerender } = render(<Frame />, { wrapper: wrapper() });
    await connect();
    await waitFor(() =>
      expect(requests.some((r) => r.operationName === "ConnectHuggingFace")).toBe(true)
    );
    identity.org = "00000000-0000-4000-8000-000000000099";
    rerender(<Frame />);
    await waitFor(() => expect(reads()).toHaveLength(2));
    const count = reads().length;
    await act(async () =>
      release(fixture({ operationName: "ConnectHuggingFace", variables: {}, query: "" }))
    );
    expect(reads()).toHaveLength(count);
    expect(
      screen.queryByText(en.models.shared.hosting.connected.replace("{account}", "hf-account"))
    ).not.toBeInTheDocument();
    expect(screen.getByLabelText(en.models.shared.hosting.token)).toHaveValue("");
  });
});
