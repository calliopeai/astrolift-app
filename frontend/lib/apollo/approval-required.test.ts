import { gql, Observable } from "@apollo/client";
import type { ApolloLink } from "@apollo/client";
import { toast } from "sonner";
import { describe, expect, it, vi } from "vitest";

import { stepUpLink } from "./links";

vi.mock("sonner", () => ({ toast: { info: vi.fn(), warning: vi.fn() } }));

function run(data: Record<string, unknown>) {
  const operation = {
    query: gql`
      mutation SoftDeleteApp {
        softDeleteApp(input: { id: "x" }) {
          ok
        }
      }
    `,
    operationName: "SoftDeleteApp",
  } as unknown as ApolloLink.Operation;
  const forward = () =>
    new Observable<ApolloLink.Result>((observer) => {
      observer.next({ data });
      observer.complete();
    });
  return new Promise<unknown>((resolve) => {
    stepUpLink.request(operation, forward)!.subscribe({ next: resolve });
  });
}

describe("APPROVAL_REQUIRED (#2202)", () => {
  it("says the action waits on approval and links to the requests, passing the result through", async () => {
    const data = {
      softDeleteApp: {
        ok: false,
        errors: [{ code: "APPROVAL_REQUIRED", message: "Deregister an app needs approval" }],
      },
    };
    await expect(run(data)).resolves.toEqual({ data });
    expect(toast.info).toHaveBeenCalledWith(
      "Deregister an app needs approval",
      expect.objectContaining({ action: expect.objectContaining({ label: "View requests" }) })
    );
  });

  it("stays quiet on other refusals", async () => {
    vi.mocked(toast.info).mockClear();
    await run({ softDeleteApp: { ok: false, errors: [{ code: "VALIDATION", message: "no" }] } });
    expect(toast.info).not.toHaveBeenCalled();
  });
});
