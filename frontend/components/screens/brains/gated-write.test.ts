import { afterEach, describe, expect, it, vi } from "vitest";

import { DEELEVATED_EVENT, ELEVATED_EVENT } from "@/lib/auth/step-up-events";

import { gatedWrite } from "./gated-write";

const refused = (code: string, extra: Record<string, unknown> = {}) => ({
  ok: false,
  errors: [{ code, message: `${code} message`, field: null, ...extra }],
  data: null,
});
const accepted = { ok: true, errors: [], data: { slug: "platform-brain" } };

afterEach(() => vi.restoreAllMocks());

describe("a write behind an action-safety gate", () => {
  it("returns what the server accepted", async () => {
    await expect(gatedWrite(async () => accepted, "failed")).resolves.toEqual({
      ok: true,
      data: { slug: "platform-brain" },
    });
  });

  it("is sent once more after the person steps up", async () => {
    const send = vi
      .fn()
      .mockResolvedValueOnce(refused("STEP_UP_REQUIRED"))
      .mockResolvedValueOnce(accepted);
    const outcome = gatedWrite(send, "failed");
    // The app-wide prompt answers on the window once the person signs in.
    await vi.waitFor(() => expect(send).toHaveBeenCalledTimes(1));
    window.dispatchEvent(new CustomEvent(ELEVATED_EVENT));
    await expect(outcome).resolves.toEqual({ ok: true, data: { slug: "platform-brain" } });
    expect(send).toHaveBeenCalledTimes(2);
  });

  it("stops when the person cancels the prompt, and says why", async () => {
    const send = vi.fn().mockResolvedValue(refused("STEP_UP_REQUIRED"));
    const outcome = gatedWrite(send, "failed");
    await vi.waitFor(() => expect(send).toHaveBeenCalledTimes(1));
    window.dispatchEvent(new CustomEvent(DEELEVATED_EVENT));
    await expect(outcome).resolves.toMatchObject({ ok: false, code: "STEP_UP_REQUIRED" });
    expect(send).toHaveBeenCalledTimes(1);
  });

  it("never asks twice: a second refusal is the answer", async () => {
    const send = vi.fn().mockResolvedValue(refused("STEP_UP_REQUIRED"));
    const outcome = gatedWrite(send, "failed");
    await vi.waitFor(() => expect(send).toHaveBeenCalledTimes(1));
    window.dispatchEvent(new CustomEvent(ELEVATED_EVENT));
    await expect(outcome).resolves.toMatchObject({ ok: false, code: "STEP_UP_REQUIRED" });
    expect(send).toHaveBeenCalledTimes(2);
  });

  it("passes an approval request through with its id", async () => {
    await expect(
      gatedWrite(async () => refused("APPROVAL_REQUIRED", { approvalRequestId: "req-1" }), "failed")
    ).resolves.toEqual({
      ok: false,
      code: "APPROVAL_REQUIRED",
      message: "APPROVAL_REQUIRED message",
      field: null,
      approvalRequestId: "req-1",
    });
  });

  it("names the field of a validation refusal, and survives a network failure", async () => {
    await expect(
      gatedWrite(async () => refused("VALIDATION", { field: "teamSlug" }), "failed")
    ).resolves.toMatchObject({ code: "VALIDATION", field: "teamSlug" });
    await expect(
      gatedWrite(async () => {
        throw new Error("Network down");
      }, "failed")
    ).resolves.toMatchObject({ ok: false, code: "NETWORK", message: "Network down" });
    await expect(gatedWrite(async () => null, "failed")).resolves.toMatchObject({
      ok: false,
      message: "failed",
    });
  });
});
