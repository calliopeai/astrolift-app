import { readFileSync } from "node:fs";
import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { buildSchema, parse, validate } from "graphql";
import { NextIntlClientProvider } from "next-intl";
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import en from "@/messages/en.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import de from "@/messages/de.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import zh from "@/messages/zh-Hans.json";
import pt from "@/messages/pt-BR.json";
import { LocalModelImportPanel } from "./LocalModelImportPanel";
import { useLocalModelImport } from "./use-local-model-import";
const identity = vi.hoisted(() => ({ org: "org-a", actor: "actor-a", allowed: true }));
vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({ org: { id: identity.org }, loading: false, error: null }),
}));
const catalogs = { en, es, fr, de, ja, ko, "zh-Hans": zh, "pt-BR": pt };
const schema = buildSchema(readFileSync("../backend/schema.graphql", "utf8"));
const files = [
  new File(["{}"], "config.json"),
  new File(["{}"], "tokenizer.json"),
  new File(["safe"], "model.safetensors"),
];
const artifact = {
  id: "artifact-a",
  name: "Local model",
  version: 1,
  state: "uploading",
  manifestSha256: "a".repeat(64),
  fileCount: 3,
  sizeBytes: "8",
};
const signedUrl = "https://private.test/store?X-Amz-Signature=PRIVATE_CAPABILITY_TEST_MARKER";
type Request = { query: string; operationName: string; variables: Record<string, unknown> };
let requests: Request[],
  transport: (request: Request) => Promise<Response>,
  puts: ReturnType<typeof vi.fn>,
  client: ApolloClient;
function response(data: Record<string, unknown>) {
  return new Response(JSON.stringify({ data }), {
    headers: { "Content-Type": "application/json" },
  });
}
function fixture(r: Request) {
  switch (r.operationName) {
    case "ListLocalModelArtifacts":
      return response({ astroliftLocalModelArtifactsPage: { items: [], nextCursor: null } });
    case "BeginLocalModelArtifact":
      return response({ beginLocalModelArtifact: { ok: true, errors: [], data: artifact } });
    case "AuthorizeLocalModelUploads":
      return response({
        authorizeLocalModelUploads: {
          ok: true,
          errors: [],
          data: {
            artifact,
            expiresInSeconds: 900,
            files: files.map((file) => ({
              name: file.name,
              sizeBytes: String(file.size),
              uploadUrl: signedUrl,
              headers: [
                { name: "Content-Type", value: "application/octet-stream" },
                { name: "x-amz-checksum-sha256", value: "PRIVATE_HEADER_TEST_MARKER" },
              ],
            })),
          },
        },
      });
    case "FinalizeLocalModelArtifact":
      return response({
        finalizeLocalModelArtifact: {
          ok: true,
          errors: [],
          data: { ...artifact, state: "verified", version: 2 },
        },
      });
    default:
      throw new Error(`Unexpected ${r.operationName}`);
  }
}
class HashWorker {
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  terminate = vi.fn();
  postMessage(data: { files: File[] }) {
    queueMicrotask(() =>
      this.onmessage?.({
        data: {
          type: "complete",
          files: data.files.map((file) => ({
            name: file.name,
            sizeBytes: String(file.size),
            sha256: "b".repeat(64),
          })),
        },
      } as MessageEvent)
    );
  }
}
function ImportContext() {
  const props = useLocalModelImport(`${identity.org}:${identity.actor}`, identity.allowed, vi.fn());
  return <LocalModelImportPanel {...props} />;
}
function Frame({ locale = "en" }: { locale?: keyof typeof catalogs }) {
  return (
    <NextIntlClientProvider locale={locale} messages={catalogs[locale]} timeZone="UTC">
      <ApolloProvider client={client}>
        <ImportContext key={`${identity.org}:${identity.actor}`} />
      </ApolloProvider>
    </NextIntlClientProvider>
  );
}
async function begin(locale: keyof typeof catalogs = "en") {
  const t = catalogs[locale].models.shared.localImport;
  await screen.findByText(t.empty);
  fireEvent.change(screen.getByLabelText(t.name), { target: { value: "Local model" } });
  fireEvent.change(screen.getByLabelText(t.files), { target: { files } });
  fireEvent.submit(screen.getByRole("button", { name: t.import }).closest("form")!);
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => {
    resolve = yes;
  });
  return { promise, resolve };
}
beforeEach(() => {
  identity.org = "org-a";
  identity.actor = "actor-a";
  identity.allowed = true;
  requests = [];
  transport = async (r) => fixture(r);
  puts = vi.fn(async () => new Response(null, { status: 200 }));
  vi.stubGlobal("fetch", puts);
  vi.stubGlobal("Worker", HashWorker);
  client = new ApolloClient({
    cache: new InMemoryCache(),
    devtools: { enabled: false },
    link: new HttpLink({
      uri: "https://control.test/gql",
      fetch: async (_uri, options) => {
        const r = JSON.parse(String(options?.body));
        expect(validate(schema, parse(r.query))).toEqual([]);
        requests.push(r);
        return transport(r);
      },
    }),
  });
});
afterEach(() => {
  client.stop();
  vi.unstubAllGlobals();
});
describe("local model import actual HttpLink boundaries", () => {
  it.each(Object.keys(catalogs) as (keyof typeof catalogs)[])(
    "%s streams exact grants, verifies and retains committed source after failed list refresh",
    async (locale) => {
      let lists = 0;
      transport = async (r) => {
        if (r.operationName === "ListLocalModelArtifacts" && ++lists > 1)
          throw new Error("source read unavailable");
        return fixture(r);
      };
      render(<Frame locale={locale} />);
      await begin(locale);
      const t = catalogs[locale].models.shared.localImport;
      await screen.findByText(t.savedRefreshFailed);
      expect(screen.getByText(t.verified)).toBeVisible();
      expect(screen.getByLabelText(t.files)).toHaveValue("");
      expect(puts).toHaveBeenCalledTimes(3);
      for (const [index, call] of puts.mock.calls.entries()) {
        const [url, options] = call as unknown as [string, RequestInit];
        expect(url).toBe(signedUrl);
        expect(options).toMatchObject({
          method: "PUT",
          body: files[index],
          credentials: "omit",
          referrerPolicy: "no-referrer",
          redirect: "error",
          cache: "no-store",
        });
        expect(options.signal).toBeInstanceOf(AbortSignal);
        expect((options.headers as Headers).get("x-amz-checksum-sha256")).toBe(
          "PRIVATE_HEADER_TEST_MARKER"
        );
      }
      expect(requests.filter((r) => r.operationName === "AuthorizeLocalModelUploads")).toHaveLength(
        3
      );
      expect(
        requests.find((r) => r.operationName === "FinalizeLocalModelArtifact")?.variables.input
      ).toEqual({ id: artifact.id, expectedVersion: 1 });
      expect(JSON.stringify(client.cache.extract())).not.toContain("PRIVATE_CAPABILITY");
      expect(document.body.innerHTML).not.toContain("PRIVATE_CAPABILITY");
      expect(document.body.innerHTML).not.toContain("PRIVATE_HEADER_TEST_MARKER");
    }
  );
  it.each(["BeginLocalModelArtifact", "AuthorizeLocalModelUploads", "FinalizeLocalModelArtifact"])(
    "refused %s retains raw refusal and prevents following writes",
    async (operation) => {
      transport = async (r) =>
        r.operationName === operation
          ? response({
              [operation[0].toLowerCase() + operation.slice(1)]: {
                ok: false,
                data: null,
                errors: [
                  {
                    code: "PERMISSION_DENIED",
                    message: "Exact server refusal",
                    field: null,
                    path: null,
                    currentVersion: null,
                    details: null,
                    supportedMethods: null,
                  },
                ],
              },
            })
          : fixture(r);
      render(<Frame />);
      await begin();
      await screen.findByText("Exact server refusal");
      expect(screen.getByLabelText(en.models.shared.localImport.name)).toHaveValue("Local model");
      expect(requests.filter((r) => r.operationName === "FinalizeLocalModelArtifact")).toHaveLength(
        operation === "FinalizeLocalModelArtifact" ? 1 : 0
      );
      expect(puts).toHaveBeenCalledTimes(operation === "FinalizeLocalModelArtifact" ? 3 : 0);
    }
  );
  it("cancel during authorize ignores late grant and performs zero PUT/finalize", async () => {
    const grant = deferred<Response>();
    transport = async (r) =>
      r.operationName === "AuthorizeLocalModelUploads" ? grant.promise : fixture(r);
    render(<Frame />);
    await begin();
    await waitFor(() =>
      expect(requests.some((r) => r.operationName === "AuthorizeLocalModelUploads")).toBe(true)
    );
    fireEvent.click(screen.getByRole("button", { name: en.models.shared.localImport.cancel }));
    await act(async () =>
      grant.resolve(
        fixture({ operationName: "AuthorizeLocalModelUploads", query: "", variables: {} })
      )
    );
    expect(puts).not.toHaveBeenCalled();
    expect(requests.some((r) => r.operationName === "FinalizeLocalModelArtifact")).toBe(false);
    expect(screen.getByText(en.models.shared.localImport.canceled)).toBeVisible();
    expect(screen.getByRole("button", { name: en.models.shared.localImport.import })).toBeEnabled();
  });
  it.each(["org", "actor"] as const)(
    "%s ABA during PUT aborts original stream and ignores late completion",
    async (binding) => {
      const put = deferred<Response>();
      puts.mockImplementationOnce(() => put.promise);
      const view = render(<Frame />);
      await begin();
      await waitFor(() => expect(puts).toHaveBeenCalledTimes(1));
      const options = puts.mock.calls[0][1] as RequestInit;
      identity[binding] = `${binding}-b`;
      view.rerender(<Frame />);
      identity[binding] = `${binding}-a`;
      identity.actor = "actor-a";
      view.rerender(<Frame />);
      expect(options.signal?.aborted).toBe(true);
      await act(async () => put.resolve(new Response(null, { status: 200 })));
      expect(puts).toHaveBeenCalledTimes(1);
      expect(requests.some((r) => r.operationName === "FinalizeLocalModelArtifact")).toBe(false);
      expect(screen.queryByText(en.models.shared.localImport.verified)).not.toBeInTheDocument();
      expect(screen.getByLabelText(en.models.shared.localImport.name)).toHaveValue("");
    }
  );
  it("source toggle during PUT unmounts import and returns with a usable empty draft", async () => {
    const held = deferred<Response>();
    puts.mockImplementationOnce(() => held.promise);
    const view = render(<Frame />);
    await begin();
    await waitFor(() => expect(puts).toHaveBeenCalledTimes(1));
    const options = puts.mock.calls[0][1] as RequestInit;
    view.rerender(<></>);
    view.rerender(<Frame />);
    expect(options.signal?.aborted).toBe(true);
    await act(async () => held.resolve(new Response(null, { status: 200 })));
    expect(requests.some((r) => r.operationName === "FinalizeLocalModelArtifact")).toBe(false);
    expect(puts).toHaveBeenCalledTimes(1);
    expect(screen.getByLabelText(en.models.shared.localImport.files)).toBeEnabled();
    expect(screen.getByLabelText(en.models.shared.localImport.name)).toHaveValue("");
    expect(screen.queryByText(en.models.shared.localImport.verified)).not.toBeInTheDocument();
  });
  it("network failures never reveal signed URLs and stay unconfirmed", async () => {
    puts.mockRejectedValue(new Error(signedUrl));
    render(<Frame />);
    await begin();
    await screen.findByText(en.models.shared.localImport.failed);
    expect(document.body.innerHTML).not.toContain("PRIVATE_CAPABILITY");
    expect(document.body.innerHTML).not.toContain("PRIVATE_HEADER_TEST_MARKER");
    expect(requests.some((r) => r.operationName === "FinalizeLocalModelArtifact")).toBe(false);
  });
  it("accepted finalization without returned metadata is not fabricated as verified", async () => {
    transport = async (r) =>
      r.operationName === "FinalizeLocalModelArtifact"
        ? response({ finalizeLocalModelArtifact: { ok: true, errors: [], data: null } })
        : fixture(r);
    render(<Frame />);
    await begin();
    await screen.findByText(en.models.shared.localImport.saved);
    expect(screen.queryByText(en.models.shared.localImport.verified)).not.toBeInTheDocument();
  });
  it("denied admission reads no artifact inventory and cannot import", async () => {
    identity.allowed = false;
    render(<Frame />);
    expect(
      screen.getByRole("button", { name: en.models.shared.localImport.import })
    ).toBeDisabled();
    expect(screen.getByLabelText(en.models.shared.localImport.files)).toBeDisabled();
    expect(requests).toEqual([]);
  });
});
