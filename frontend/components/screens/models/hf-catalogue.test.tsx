import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { PropsWithChildren } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { buildSchema, validate } from "graphql";
import { readFileSync } from "node:fs";
import en from "@/messages/en.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import de from "@/messages/de.json";
import pt from "@/messages/pt-BR.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import zh from "@/messages/zh-Hans.json";
const locales = { en, es, fr, de, "pt-BR": pt, ja, ko, "zh-Hans": zh };
import {
  SEARCH_HUGGING_FACE_MODELS,
  GET_HUGGING_FACE_MODEL,
} from "@/graphql/models/catalogue.queries";
import type { HuggingFaceModel } from "@/graphql/__generated__/schema";
import { HuggingFaceCataloguePanel } from "./HuggingFaceCataloguePanel";
import { useHfCatalogue } from "./use-hf-catalogue";
import { hfCatalogueProps } from "./shared-model.fixtures";

const org = vi.hoisted(() => ({ id: "org-one" }));
vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({ org: { id: org.id }, loading: false, error: null }),
}));
const model: HuggingFaceModel = { ...hfCatalogueProps.page.rows[0], revisionSha: "b".repeat(40) };
type Request = { operationName: string; variables: Record<string, unknown> };
let requests: Request[], transport: (request: Request) => Promise<Response>;
function response(data: Record<string, unknown>) {
  return new Response(JSON.stringify({ data }), {
    headers: { "Content-Type": "application/json" },
  });
}
function wrapper(locale: keyof typeof locales = "en") {
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new HttpLink({
      uri: "http://control-plane.test/app/gql/config/",
      fetch: vi.fn(async (_uri, options) => {
        const request: Request = JSON.parse(String(options?.body));
        requests.push(request);
        return transport(request);
      }),
    }),
  });
  return function Provider({ children }: PropsWithChildren) {
    return (
      <NextIntlClientProvider locale={locale} messages={locales[locale]} timeZone="UTC">
        <ApolloProvider client={client}>{children}</ApolloProvider>
      </NextIntlClientProvider>
    );
  };
}
function Frame({
  onPinned,
}: {
  onPinned: (model: { repoId: string; revisionSha: string }) => void;
}) {
  return <HuggingFaceCataloguePanel {...useHfCatalogue(onPinned)} />;
}

beforeEach(() => {
  org.id = "org-one";
  requests = [];
  transport = async (request) =>
    request.operationName === "SearchHuggingFaceModels"
      ? response({
          astroliftHuggingFaceModels: {
            state: "AVAILABLE",
            source: "https://huggingface.co/api/models",
            observedAt: "2026-09-30T15:30:00Z",
            nextCursor: request.variables.after ? null : "opaque-hf-page",
            retryAfterSeconds: null,
            items: [
              {
                ...model,
                repoId: request.variables.after ? "publisher/later-model" : model.repoId,
                revisionSha: null,
              },
            ],
          },
        })
      : response({
          astroliftHuggingFaceModel: {
            state: "AVAILABLE",
            source: "https://huggingface.co/api/models",
            observedAt: "2026-09-30T15:30:00Z",
            model: { ...model, repoId: request.variables.repoId },
            retryAfterSeconds: null,
          },
        });
});

describe("real HF catalogue adapter", () => {
  it("validates both documents against the backend-produced SDL", () => {
    const schema = buildSchema(readFileSync(`${process.cwd()}/schema.graphql`, "utf8"));
    expect(validate(schema, SEARCH_HUGGING_FACE_MODELS)).toEqual([]);
    expect(validate(schema, GET_HUGGING_FACE_MODEL)).toEqual([]);
  });
  it("walks the actual opaque cursor and sends debounced search to the server", async () => {
    render(<Frame onPinned={vi.fn()} />, { wrapper: wrapper() });
    await screen.findByRole("button", { name: model.repoId });
    fireEvent.click(screen.getByRole("button", { name: "Older" }));
    expect(
      await screen.findByRole("button", { name: "publisher/later-model" })
    ).toBeInTheDocument();
    expect(requests[1].variables).toMatchObject({ after: "opaque-hf-page", first: 20 });
    fireEvent.change(screen.getByPlaceholderText("Search models on Hugging Face"), {
      target: { value: "embed" },
    });
    fireEvent.change(screen.getByPlaceholderText("Search models on Hugging Face"), {
      target: { value: "embedding" },
    });
    await waitFor(() =>
      expect(requests.at(-1)?.variables).toMatchObject({ search: "embedding", after: null })
    );
    expect(requests.filter((request) => request.variables.search === "embed")).toHaveLength(0);
    expect(requests.every((request) => request.operationName === "SearchHuggingFaceModels")).toBe(
      true
    );
  });
  it("requests publisher/task/library/license/gated/order filters without fabricating compute compatibility", async () => {
    const { result } = renderHook(() => useHfCatalogue(vi.fn()), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.page.rows.length).toBe(1));
    act(() => {
      result.current.page.list.applySearch("qwen", {
        author: "Qwen",
        pipelineTag: "text-generation",
        library: "transformers",
        license: "apache-2.0",
        gated: "true",
        ordering: "likes",
      });
    });
    await waitFor(() =>
      expect(requests.at(-1)?.variables).toEqual({
        search: "qwen",
        author: "Qwen",
        pipelineTag: "text-generation",
        library: "transformers",
        license: "apache-2.0",
        gated: true,
        sortBy: "likes",
        first: 20,
        after: null,
      })
    );
    expect(result.current.page.rows[0].compatibility).toBe("UNKNOWN");
    expect(requests.at(-1)?.variables).not.toHaveProperty("computeMode");
  });
  it("resolves a repository through the actual detail query before handing off its immutable SHA", async () => {
    const onPinned = vi.fn();
    render(<Frame onPinned={onPinned} />, { wrapper: wrapper() });
    fireEvent.click(await screen.findByRole("button", { name: model.repoId }));
    const use = await screen.findByRole("button", { name: "Use verified revision" });
    await waitFor(() => expect(use).toBeEnabled());
    expect(requests.at(-1)).toMatchObject({
      operationName: "GetHuggingFaceModel",
      variables: { repoId: model.repoId, revision: null },
    });
    fireEvent.click(use);
    expect(onPinned).toHaveBeenCalledExactlyOnceWith({
      repoId: model.repoId,
      revisionSha: "b".repeat(40),
    });
    expect(
      requests.every((request) => !request.operationName.toLowerCase().includes("deploy"))
    ).toBe(true);
  });
  it("blocks a moving branch immediately while its new immutable revision is resolved", async () => {
    let resolve: ((response: Response) => void) | undefined;
    const initial = transport;
    transport = async (request) =>
      request.variables.revision === "release"
        ? new Promise<Response>((done) => {
            resolve = done;
          })
        : initial(request);
    const onPinned = vi.fn();
    render(<Frame onPinned={onPinned} />, { wrapper: wrapper() });
    fireEvent.click(await screen.findByRole("button", { name: model.repoId }));
    const use = await screen.findByRole("button", { name: "Use verified revision" });
    await waitFor(() => expect(use).toBeEnabled());
    fireEvent.change(screen.getByLabelText("Revision, branch or tag"), {
      target: { value: "release" },
    });
    expect(use).toBeDisabled();
    await waitFor(() => expect(resolve).toBeDefined());
    await act(async () =>
      resolve?.(
        response({
          astroliftHuggingFaceModel: {
            state: "AVAILABLE",
            source: "https://huggingface.co/api/models",
            observedAt: "2026-09-30T15:40:00Z",
            model: { ...model, revisionSha: "c".repeat(40) },
            retryAfterSeconds: null,
          },
        })
      )
    );
    await waitFor(() => expect(use).toBeEnabled());
    fireEvent.click(use);
    expect(onPinned).toHaveBeenCalledExactlyOnceWith({
      repoId: model.repoId,
      revisionSha: "c".repeat(40),
    });
  });
  it.each(["RATE_LIMITED", "UNAVAILABLE"] as const)(
    "reports %s without turning unavailability into an empty catalogue",
    async (state) => {
      transport = async () =>
        response({
          astroliftHuggingFaceModels: {
            state,
            source: "https://huggingface.co/api/models",
            observedAt: "2026-09-30T15:30:00Z",
            retryAfterSeconds: state === "RATE_LIMITED" ? 45 : null,
            nextCursor: null,
            items: [],
          },
        });
      render(<Frame onPinned={vi.fn()} />, { wrapper: wrapper() });
      expect(
        await screen.findByText(
          state === "RATE_LIMITED"
            ? en.models.shared.catalogue.rateLimited
            : en.models.shared.catalogue.unavailable
        )
      ).toBeInTheDocument();
      expect(screen.queryByText("No catalogue matches")).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: model.repoId })).not.toBeInTheDocument();
    }
  );
  it("blocks missing or foreign detail revisions instead of carrying an unverified repository into deployment", async () => {
    const initial = transport;
    transport = async (request) =>
      request.operationName === "GetHuggingFaceModel"
        ? response({
            astroliftHuggingFaceModel: {
              state: "AVAILABLE",
              source: "https://huggingface.co/api/models",
              observedAt: "2026-09-30T15:30:00Z",
              model: { ...model, repoId: "foreign/repo", revisionSha: null },
              retryAfterSeconds: null,
            },
          })
        : initial(request);
    const onPinned = vi.fn();
    render(<Frame onPinned={onPinned} />, { wrapper: wrapper() });
    fireEvent.click(await screen.findByRole("button", { name: model.repoId }));
    expect(
      await screen.findByText("No immutable revision has been verified for this selection.")
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Use verified revision" })).toBeDisabled();
    expect(onPinned).not.toHaveBeenCalled();
  });
  it.each(Object.keys(locales) as (keyof typeof locales)[])(
    "uses %s presentation without changing selected repo/SHA",
    async (locale) => {
      const onPinned = vi.fn();
      const messages = locales[locale].models.shared.catalogue;
      render(<Frame onPinned={onPinned} />, { wrapper: wrapper(locale) });
      expect(await screen.findByPlaceholderText(messages.search)).toBeInTheDocument();
      fireEvent.click(await screen.findByRole("button", { name: model.repoId }));
      const use = await screen.findByRole("button", { name: messages.useRevision });
      await waitFor(() => expect(use).toBeEnabled());
      expect(
        screen.getByText(messages.compatibilityUnknown + ". " + messages.accessNotice)
      ).toBeInTheDocument();
      fireEvent.click(use);
      expect(onPinned).toHaveBeenCalledExactlyOnceWith({
        repoId: model.repoId,
        revisionSha: "b".repeat(40),
      });
    }
  );

  it("clears the selected revision across organization A→B→A", async () => {
    const { result, rerender } = renderHook(() => useHfCatalogue(vi.fn()), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.page.rows.length).toBe(1));
    act(() => result.current.onSelect(model.repoId));
    await waitFor(() => expect(result.current.resolvedModel?.revisionSha).toBe(model.revisionSha));
    org.id = "org-two";
    rerender();
    expect(result.current.selectedRepoId).toBeNull();
    org.id = "org-one";
    rerender();
    expect(result.current.selectedRepoId).toBeNull();
    expect(result.current.resolvedModel).toBeNull();
  });
});
