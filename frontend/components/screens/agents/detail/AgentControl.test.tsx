import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AgentControlScreen } from "./AgentControl";
import { CONTROL, CONTROL_AGENT, PERSISTED_SERVICE_SPEC } from "./agent-control.fixtures";

describe("stored agent run settings", () => {
  it("reads replicas and schedules before saving, and sends those stored settings", async () => {
    const onSave = vi.fn(async () => ({ ok: true as const, persisted: null }));
    render(
      <AgentControlScreen
        {...CONTROL}
        agent={{ ...CONTROL_AGENT, ...PERSISTED_SERVICE_SPEC, replicas: 7 }}
        onSave={onSave}
        triggerEditor={null}
      />
    );
    expect(screen.getByRole("slider", { name: "Replicas" })).toHaveValue("7");
    expect(screen.getByDisplayValue("0 8 * * 1-5")).toBeInTheDocument();
    expect(screen.getByDisplayValue("0 18 * * 1-5")).toBeInTheDocument();
    expect(screen.getByDisplayValue("6")).toBeInTheDocument();
    expect(screen.queryByText(/save to set/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Save run spec/ }));
    await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
    expect(onSave.mock.calls[0]).toEqual([
      expect.objectContaining({
        replicas: 7,
        scheduledScaleTo: 6,
        scaleUpCron: "0 8 * * 1-5",
        scaleDownCron: "0 18 * * 1-5",
        clearScheduledScaling: false,
      }),
    ]);
  });

  it("preserves a stored zero and a draft during polling, but seeds a new agent", () => {
    const agent = { ...CONTROL_AGENT, runMode: "loop", runMaxParallel: 0 };
    const view = render(<AgentControlScreen {...CONTROL} agent={agent} triggerEditor={null} />);
    const cap = screen.getByRole("spinbutton", { name: "Concurrency cap" });
    expect(cap).toHaveValue(0);
    fireEvent.change(cap, { target: { value: "3" } });
    view.rerender(
      <AgentControlScreen
        {...CONTROL}
        agent={{ ...agent, runMaxParallel: 9 }}
        triggerEditor={null}
      />
    );
    expect(cap).toHaveValue(3);
    view.rerender(
      <AgentControlScreen
        {...CONTROL}
        agent={{ ...agent, id: "other", runMaxParallel: null }}
        triggerEditor={null}
      />
    );
    expect(cap).toHaveValue(null);
  });
});
