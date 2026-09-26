import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ModelReplicas } from "./model-replicas";

const update = vi.fn().mockResolvedValue({ data: { updateManagedService: { ok: true } } });
vi.mock("@apollo/client/react", () => ({ useMutation: () => [update, { loading: false }] }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

describe("ModelReplicas", () => {
  it("stops and scales by sending the whole config with only replicas changed", async () => {
    const onChanged = vi.fn();
    render(
      <ModelReplicas
        id="m1"
        name="qwen"
        config={{ model: "Qwen/Qwen3-8B", gpu: 2 }}
        onChanged={onChanged}
      />
    );

    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(update).toHaveBeenLastCalledWith({
      variables: { input: { id: "m1", config: { model: "Qwen/Qwen3-8B", gpu: 2, replicas: 0 } } },
    });

    fireEvent.click(screen.getByRole("button", { name: "Scale qwen up" }));
    await waitFor(() => expect(update).toHaveBeenCalledTimes(2));
    expect(update.mock.lastCall?.[0].variables.input.config.replicas).toBe(2);
    expect(screen.getByRole("button", { name: "Scale qwen down" })).toBeDisabled();
  });

  it("offers Start for a stopped model", () => {
    render(
      <ModelReplicas id="m1" name="qwen" config={{ model: "m", replicas: 0 }} onChanged={vi.fn()} />
    );
    expect(screen.getByRole("button", { name: "Start" })).toBeEnabled();
  });
});
