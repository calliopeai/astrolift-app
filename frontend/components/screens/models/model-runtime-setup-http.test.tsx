import { readFileSync } from "node:fs";
import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { buildSchema, parse, validate } from "graphql";
import { NextIntlClientProvider } from "next-intl";
import { useLayoutEffect } from "react";
import { beforeEach, expect, it, vi } from "vitest";
import en from "@/messages/en.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import de from "@/messages/de.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import zh from "@/messages/zh-Hans.json";
import pt from "@/messages/pt-BR.json";
import { ModelRuntimeSetupPanel, type ModelRuntimeSetupProps } from "./ModelRuntimeSetupPanel";
import { runtimeSetupFixture } from "./ModelRuntimeSetupPanel.stories";
import { useModelRuntimeSetup } from "./use-model-runtime-setup";
const locales = { en, es, fr, de, ja, ko, "zh-Hans": zh, "pt-BR": pt };
const schema = buildSchema(readFileSync("../backend/schema.graphql", "utf8"));
type Request = { operationName: string; query: string; variables: Record<string, unknown> };
let requests: Request[],
  readCount: number,
  mode: "success" | "refused" | "refreshFailed" | "initialFailed" | "held";
let release: (value: Response) => void;
let latest: ModelRuntimeSetupProps;
const refresh = vi.fn(async () => {});
function response(data: unknown) {
  return new Response(JSON.stringify({ data }), {
    headers: { "content-type": "application/json" },
  });
}
const target = {
  organizationId: "00000000-0000-4000-8000-000000000001",
  clusterId: "00000000-0000-4000-8000-000000000002",
  expectedProviderId: "00000000-0000-4000-8000-000000000003",
};
const observation = {
  ...runtimeSetupFixture.observation!,
  organizationId: target.organizationId,
  clusterId: target.clusterId,
  providerId: target.expectedProviderId,
  observedAt: "2026-10-03T12:00:00Z",
  modes: runtimeSetupFixture.observation!.modes.map((row) => ({
    ...row,
    hardwareAdmission: "operator_declared",
  })),
};
function envelope() {
  return response({
    updateClusterModelRuntime: {
      ok: true,
      errors: [],
      data: { ...observation, clusterVersion: 8 },
    },
  });
}
function Context({ scope, allowed }: { scope: string; allowed: boolean }) {
  const props = useModelRuntimeSetup(target, scope, allowed, "CPU", refresh);
  useLayoutEffect(() => {
    latest = props;
  }, [props]);
  return <ModelRuntimeSetupPanel {...props} />;
}
function view(
  locale: keyof typeof locales = "en",
  scope = "actor:org",
  allowed = true,
  keyed = true
) {
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    devtools: { enabled: false },
    link: new HttpLink({
      uri: "https://product.test/gql",
      fetch: async (_uri, options) => {
        const request = JSON.parse(String(options?.body)) as Request;
        expect(validate(schema, parse(request.query))).toEqual([]);
        requests.push(request);
        if (request.operationName === "GetClusterModelRuntimeSettings") {
          readCount++;
          if ((mode === "refreshFailed" && readCount > 1) || mode === "initialFailed")
            throw new Error("Literal provider read failed");
          return response({ clusterModelRuntimeSettings: observation });
        }
        if (mode === "held")
          return new Promise<Response>((resolve) => {
            release = resolve;
          });
        if (mode === "refused")
          return response({
            updateClusterModelRuntime: {
              ok: false,
              errors: [
                {
                  code: "VERSION_MISMATCH",
                  message: "Literal provider version refused",
                  currentVersion: 4,
                },
              ],
              data: null,
            },
          });
        return envelope();
      },
    }),
  });
  const element = (s = scope, a = allowed) => (
    <NextIntlClientProvider locale={locale} messages={locales[locale]} timeZone="UTC">
      <ApolloProvider client={client}>
        <Context key={keyed ? s : "retained"} scope={s} allowed={a} />
      </ApolloProvider>
    </NextIntlClientProvider>
  );
  return { element, client };
}
async function open(locale: keyof typeof locales = "en") {
  const copy = locales[locale].models.shared.runtimeSetup;
  await waitFor(() => expect(screen.getByRole("button", { name: copy.configure })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: copy.configure }));
  return copy;
}
beforeEach(() => {
  requests = [];
  readCount = 0;
  mode = "success";
  refresh.mockClear();
});
it.each(Object.keys(locales) as (keyof typeof locales)[])(
  "%s actual API save remains accepted after failed follow-up read",
  async (locale) => {
    mode = "refreshFailed";
    const { element } = view(locale);
    render(element());
    const copy = await open(locale);
    fireEvent.click(screen.getByRole("button", { name: copy.save }));
    expect(await screen.findByText(copy.savedRefreshFailed)).toBeVisible();
    const write = requests.find((row) => row.operationName === "UpdateClusterModelRuntime")!;
    expect(write.variables.input).toMatchObject({
      ...target,
      ifMatchVersion: 7,
      expectedProviderVersion: 3,
      computeMode: "CPU",
      declaration: {
        defaultDtype: "FLOAT32",
        defaultMaxModelLen: 256,
        defaultMaxNumSeqs: 1,
        hardwareCertified: false,
        hardwareAttested: false,
      },
    });
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(copy.failed)).not.toBeInTheDocument();
  }
);
it("refusal retains exact draft and causes no read or admission refresh", async () => {
  mode = "refused";
  const { element } = view();
  render(element());
  const copy = await open();
  fireEvent.change(screen.getByLabelText(copy.hardwareEvidence), {
    target: { value: "operator evidence draft" },
  });
  fireEvent.click(screen.getByRole("button", { name: copy.save }));
  expect(await screen.findByText("Literal provider version refused")).toBeVisible();
  expect(readCount).toBe(1);
  expect(refresh).not.toHaveBeenCalled();
  expect(screen.getByLabelText(copy.hardwareEvidence)).toHaveValue("operator evidence draft");
});
it("failed first read is unavailable until actual retry succeeds", async () => {
  mode = "initialFailed";
  const { element } = view();
  render(element());
  const copy = en.models.shared.runtimeSetup;
  expect(await screen.findByText("Literal provider read failed")).toBeVisible();
  expect(screen.getByRole("button", { name: copy.configure })).toBeDisabled();
  mode = "success";
  fireEvent.click(screen.getByRole("button", { name: copy.retry }));
  await waitFor(() => expect(screen.getByRole("button", { name: copy.configure })).toBeEnabled());
  expect(requests.every((row) => row.operationName === "GetClusterModelRuntimeSettings")).toBe(
    true
  );
});
it("same-target failed refresh retains observation and draft but blocks a new write", async () => {
  const { element } = view();
  render(element());
  const copy = await open();
  fireEvent.change(screen.getByLabelText(copy.image), {
    target: { value: "registry.example/runtime@sha256:" + "b".repeat(64) },
  });
  mode = "initialFailed";
  act(() => latest.onRetry());
  expect(await screen.findByText("Literal provider read failed")).toBeVisible();
  expect(screen.getByLabelText(copy.image)).toHaveValue(
    "registry.example/runtime@sha256:" + "b".repeat(64)
  );
  expect(screen.getByRole("button", { name: copy.save })).toBeDisabled();
});
it("withdrawn authority skips source read and cannot submit old callback", async () => {
  const { element } = view();
  const mounted = render(element());
  await open();
  const old = latest.onSave;
  mounted.rerender(element("actor:org", false));
  await act(async () => {
    expect(
      await old(runtimeSetupFixture.observation!.modes[0].declaration! as never, observation)
    ).toMatchObject({ accepted: false });
  });
  expect(requests.filter((row) => row.operationName === "UpdateClusterModelRuntime")).toHaveLength(
    0
  );
  expect(
    screen.getByRole("button", { name: en.models.shared.runtimeSetup.configure })
  ).toBeDisabled();
});
it("actor/source ABA drops reviewed draft and late reply cannot refresh or mark new source saved", async () => {
  mode = "held";
  const { element } = view();
  const mounted = render(element());
  const copy = await open();
  fireEvent.click(screen.getByRole("button", { name: copy.save }));
  await waitFor(() =>
    expect(
      requests.filter((row) => row.operationName === "UpdateClusterModelRuntime")
    ).toHaveLength(1)
  );
  mounted.rerender(element("actor-two:org"));
  await open();
  mounted.rerender(element("actor:org"));
  await open();
  await act(async () => release(envelope()));
  expect(refresh).not.toHaveBeenCalled();
  expect(screen.queryByText(copy.saved)).not.toBeInTheDocument();
  expect(screen.getByLabelText(copy.hardwareEvidence)).toHaveValue("");
});

it("retained hook binding epoch refuses old callbacks and suppresses held-response refresh after scope ABA", async () => {
  mode = "held";
  const { element } = view("en", "actor:org", true, false);
  const mounted = render(element());
  const copy = await open();
  const old = latest.onSave;
  fireEvent.click(screen.getByRole("button", { name: copy.save }));
  await waitFor(() =>
    expect(
      requests.filter((row) => row.operationName === "UpdateClusterModelRuntime")
    ).toHaveLength(1)
  );
  mounted.rerender(element("actor-two:org"));
  await open();
  mounted.rerender(element("actor:org"));
  await open();
  await act(async () => release(envelope()));
  expect(refresh).not.toHaveBeenCalled();
  expect(readCount).toBe(1);
  expect(screen.queryByText(copy.saved)).not.toBeInTheDocument();
  await act(async () => {
    expect(
      await old(runtimeSetupFixture.observation!.modes[0].declaration! as never, observation)
    ).toMatchObject({ accepted: false });
  });
  expect(requests.filter((row) => row.operationName === "UpdateClusterModelRuntime")).toHaveLength(
    1
  );
});
