import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReviewedStartSheetProps } from "./ReviewedStartSheet";
import { useReviewedStart } from "./use-reviewed-start";

const fixture = vi.hoisted(() => ({
  org: "disposable-org",
  actor: "disposable-actor",
  query: vi.fn(),
  mutate: vi.fn(),
}));
vi.mock("@apollo/client/react", () => ({
  useApolloClient: () => ({ query: fixture.query, mutate: fixture.mutate }),
  useQuery: () => ({ data: { me: { id: fixture.actor } } }),
}));
vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({ org: { id: fixture.org } }),
}));
vi.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));

const review = {
  data: {
    workflowDefinitionById: {
      guid: "exact-definition",
      revision: "reviewed-revision",
      definition: { name: "Disposable", slug: "disposable", isEnabled: true, stageCount: 1 },
      inputContract: {
        schema: { type: "object" },
        digest: "schema-digest",
        supported: true,
        error: "",
        fields: [],
        acceptsInputs: true,
        supportsSimpleForm: false,
      },
    },
  },
};

function props(result: { current: ReturnType<typeof useReviewedStart> }) {
  return result.current.dialog!.props as ReviewedStartSheetProps;
}

beforeEach(() => {
  sessionStorage.clear();
  fixture.org = "disposable-org";
  fixture.actor = "disposable-actor";
  fixture.query.mockReset().mockResolvedValue(review);
  fixture.mutate.mockReset().mockRejectedValue(new Error("Response lost"));
});

describe("reviewed start request ownership", () => {
  it("retries the same caller key and original body while storing metadata only", async () => {
    const { result } = renderHook(() => useReviewedStart("workflow"));
    await act(async () => result.current.open("exact-definition"));
    await act(async () => props(result).onSubmit({ privateInput: "PRIVATE-INPUT-MARKER" }, ""));
    const first = fixture.mutate.mock.calls[0][0].variables.input;
    act(() => props(result).onClose());
    await act(async () => result.current.open("exact-definition"));
    await act(async () => props(result).onSubmit({ privateInput: "Changed input" }, ""));
    const second = fixture.mutate.mock.calls[1][0].variables.input;
    expect(second).toEqual(first);
    expect(first.requestId).toBeTruthy();
    const stored = Array.from({ length: sessionStorage.length }, (_, index) =>
      sessionStorage.getItem(sessionStorage.key(index)!)
    ).join("");
    expect(stored).toContain(first.requestId);
    expect(stored).not.toContain("PRIVATE-INPUT-MARKER");
    expect(stored).not.toContain("privateInput");
  });

  it("a reload reconciles the original key without submitting persisted input values", async () => {
    const original = renderHook(() => useReviewedStart("workflow"));
    await act(async () => original.result.current.open("exact-definition"));
    await act(async () =>
      props(original.result).onSubmit({ privateInput: "PRIVATE-INPUT-MARKER" }, "")
    );
    const key = fixture.mutate.mock.calls[0][0].variables.input.requestId;
    original.unmount();
    fixture.query.mockResolvedValueOnce(review).mockResolvedValueOnce({
      data: { workflowDefinitionStartRequest: null },
    });
    const reloaded = renderHook(() => useReviewedStart("workflow"));
    await act(async () => reloaded.result.current.open("exact-definition"));
    expect(fixture.mutate).toHaveBeenCalledTimes(1);
    expect(fixture.query.mock.lastCall?.[0].variables).toEqual({ requestId: key });
    expect(props(reloaded.result).restored).toBe(true);
    expect(props(reloaded.result).uncertain).toBe(true);
  });

  it("discards an old organization's delayed review response after context changes", async () => {
    let resolve!: (response: typeof review) => void;
    fixture.query.mockImplementationOnce(
      () =>
        new Promise((done) => {
          resolve = done;
        })
    );
    const { result, rerender } = renderHook(() => useReviewedStart("workflow"));
    let pending!: Promise<void>;
    act(() => {
      pending = result.current.open("exact-definition");
    });
    fixture.org = "different-org";
    rerender();
    await act(async () => {
      resolve(review);
      await pending;
    });
    expect(result.current.dialog).toBeNull();
    expect(result.current.busy).toBe(false);
    expect(fixture.mutate).not.toHaveBeenCalled();
  });
});
