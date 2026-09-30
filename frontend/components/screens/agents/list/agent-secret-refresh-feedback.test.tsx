import { readFileSync } from "node:fs";
import path from "node:path";
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
import { createTranslator, NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { Observable, type Subscriber } from "rxjs";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { locales } from "@/i18n/config";
import { ATTACHMENT, BUNDLES_LIST, SECRET_ROWS, SECRETS } from "./agents-dispatch-secrets.fixtures";
import { AgentSecretsView } from "./AgentSecrets";
import { useAgentSecretBundles } from "./use-agent-secret-bundles";
import { useAgentSecrets } from "./use-agent-secrets";
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn() }));
vi.mock("sonner", () => ({ toast }));
vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({ org: { id: "actual-org" } }),
}));
beforeEach(() => {
  vi.clearAllMocks();
});
type Values = ReturnType<typeof useAgentSecrets>;
type Bundles = ReturnType<typeof useAgentSecretBundles>;
type Observer = Subscriber<{ data: Record<string, unknown> }>;
const recipe = "explicit-selected-recipe";
const bundle = BUNDLES_LIST[0];
const row = SECRET_ROWS[0];
const cases: Array<{
  operation: string;
  invoke: (values: Values, bundles: Bundles) => Promise<boolean | void>;
  returnsTrue?: boolean;
}> = [
  {
    operation: "SetAgentSecretValue",
    invoke: (v) => v.onSave(row.envVar, "actual-local-value"),
    returnsTrue: true,
  },
  { operation: "DeleteAgentSecretValue", invoke: (v) => v.onDeleteValue(row) },
  {
    operation: "UpsertAgentSecretRef",
    invoke: (v) => v.onUpsertRef(row.envVar, row.uri),
    returnsTrue: true,
  },
  { operation: "RemoveAgentSecretRef", invoke: (v) => v.onRemoveRef(row) },
  {
    operation: "CreateAgentSecretBundle",
    invoke: (_, b) => b.onCreate("Actual bundle", "actual-bundle", ""),
    returnsTrue: true,
  },
  {
    operation: "UpdateAgentSecretBundle",
    invoke: (_, b) => b.onUpdate(bundle, "Updated bundle", ""),
    returnsTrue: true,
  },
  { operation: "DeleteAgentSecretBundle", invoke: (_, b) => b.onDeleteBundle(bundle) },
  {
    operation: "AttachAgentSecretBundle",
    invoke: (_, b) => b.onAttach(bundle, "ACTUAL_", 2),
    returnsTrue: true,
  },
  { operation: "DetachAgentSecretBundle", invoke: (_, b) => b.onDetach(ATTACHMENT) },
  {
    operation: "SetAgentBundleSecretValue",
    invoke: (_, b) => b.onSetKey(bundle, "ACTUAL_KEY", "actual-local-value"),
    returnsTrue: true,
  },
  {
    operation: "DeleteAgentBundleSecretValue",
    invoke: (_, b) => b.onDeleteKey(bundle, "ACTUAL_KEY"),
  },
];
function transport(locale: string) {
  const messages = JSON.parse(readFileSync(path.resolve("messages", `${locale}.json`), "utf8"));
  const requests: Operation[] = [];
  let committed = false;
  let readsAvailable = false;
  let write: { operation: Operation; observer: Observer } | undefined;
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new ApolloLink(
      (operation) =>
        new Observable((observer) => {
          requests.push(operation);
          const definition = operation.query.definitions.find(
            (d) => d.kind === "OperationDefinition"
          );
          if (definition?.kind === "OperationDefinition" && definition.operation === "mutation")
            write = { operation, observer };
          else if (committed && !readsAvailable)
            observer.error(new Error("REFRESH_TRANSPORT_UNAVAILABLE"));
          else {
            const data =
              operation.operationName === "AgentEnvironmentSpecSecretStatus"
                ? { agentEnvironmentSpecSecretStatus: SECRET_ROWS }
                : operation.operationName === "AgentSecretBundles"
                  ? { agentSecretBundles: BUNDLES_LIST }
                  : { agentEnvironmentSpecSecretBundleAttachments: [ATTACHMENT] };
            observer.next({ data });
            observer.complete();
          }
        })
    ),
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <NextIntlClientProvider locale={locale} messages={messages}>
      <ApolloProvider client={client}>{children}</ApolloProvider>
    </NextIntlClientProvider>
  );
  return {
    client,
    wrapper,
    requests,
    messages,
    commit: () => {
      expect(write).toBeDefined();
      committed = true;
      const name = write!.operation.operationName!;
      const root = name[0].toLowerCase() + name.slice(1);
      const data = name.includes("Bundle")
        ? name.includes("Attach") || name.includes("Detach")
          ? ATTACHMENT
          : bundle
        : { envVar: row.envVar, uri: row.uri, exists: true };
      write!.observer.next({ data: { [root]: { ok: true, errors: [], data } } });
      write!.observer.complete();
    },
    isCommitted: () => committed,
    recoverReads: () => {
      readsAvailable = true;
    },
  };
}
async function verifyCommittedWrite(entry: (typeof cases)[number], locale: string) {
  const io = transport(locale);
  const t = createTranslator({ locale, messages: io.messages, namespace: "agentSecrets.feedback" });
  const values = renderHook(() => useAgentSecrets(recipe, true), { wrapper: io.wrapper });
  const bundles = renderHook(() => useAgentSecretBundles(recipe, true), { wrapper: io.wrapper });
  await waitFor(() => expect(values.result.current.rows).toHaveLength(SECRET_ROWS.length));
  await waitFor(() => expect(bundles.result.current.defaultAttachments).toHaveLength(1));
  let result: boolean | void;
  let pending: Promise<boolean | void>;
  act(() => {
    pending = entry.invoke(values.result.current, bundles.result.current);
    void entry.invoke(values.result.current, bundles.result.current);
  });
  expect(io.requests.filter((r) => r.operationName === entry.operation)).toHaveLength(1);
  await act(async () => {
    io.commit();
    result = await pending;
  });
  if (entry.returnsTrue) expect(result!).toBe(true);
  expect(io.isCommitted()).toBe(true);
  expect(toast.success).toHaveBeenCalledOnce();
  expect(toast.warning).toHaveBeenCalledExactlyOnceWith(t("refreshFailed"));
  expect(toast.error).not.toHaveBeenCalled();
  expect(values.result.current.pending.size).toBe(0);
  expect(bundles.result.current.pending.size).toBe(0);
  expect(io.requests.every((r) => r.variables.slug === recipe)).toBe(true);
  const readCount = io.requests.length;
  act(() => {
    if (entry.operation.includes("Bundle")) bundles.result.current.onRetry();
    else values.result.current.onRetry();
  });
  await waitFor(() => expect(io.requests.length).toBeGreaterThan(readCount));
  await waitFor(() =>
    expect(toast.error).toHaveBeenCalledExactlyOnceWith(
      t("failure", { operation: t("readFailed"), message: "REFRESH_TRANSPORT_UNAVAILABLE" })
    )
  );
  expect(io.requests.filter((r) => r.operationName === entry.operation)).toHaveLength(1);
  expect(io.isCommitted()).toBe(true);
  values.unmount();
  bundles.unmount();
  io.client.stop();
}
describe("committed agent secret writes with failed refresh", () => {
  it("clears a committed plaintext draft after a read-only UI retry", async () => {
    const io = transport("fr");
    const t = createTranslator({
      locale: "fr",
      messages: io.messages,
      namespace: "agentSecrets.values",
    });
    const shared = createTranslator({
      locale: "fr",
      messages: io.messages,
      namespace: "shared.table",
    });
    function Screen() {
      return <AgentSecretsView {...SECRETS} {...useAgentSecrets(recipe, true)} embedded />;
    }
    const view = render(<Screen />, { wrapper: io.wrapper });
    const input = (await screen.findAllByPlaceholderText(t("rotatePlaceholder")))[0];
    fireEvent.change(input, { target: { value: "actual-local-plaintext-draft" } });
    fireEvent.click(within(input.parentElement!).getByRole("button", { name: t("rotate") }));
    await act(async () => {
      io.commit();
    });
    await waitFor(() => expect(toast.warning).toHaveBeenCalledOnce());
    expect(toast.success).toHaveBeenCalledOnce();
    expect(toast.error).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("REFRESH_TRANSPORT_UNAVAILABLE");
    io.recoverReads();
    fireEvent.click(screen.getByRole("button", { name: shared("retry") }));
    await waitFor(() =>
      expect(screen.getAllByPlaceholderText(t("rotatePlaceholder"))[0]).toHaveValue("")
    );
    expect(io.requests.filter((r) => r.operationName === "SetAgentSecretValue")).toHaveLength(1);
    expect(io.isCommitted()).toBe(true);
    view.unmount();
    io.client.stop();
  });
  it.each(cases)("keeps $operation committed and retries only reads", async (entry) => {
    await verifyCommittedWrite(entry, "en");
  });
  it.each(locales)("%s warns about refreshing without suggesting another write", async (locale) => {
    await verifyCommittedWrite(cases[0], locale);
  });
});
