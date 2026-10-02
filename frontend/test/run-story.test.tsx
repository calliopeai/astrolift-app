import { composeStory } from "@storybook/react";
import { cleanup } from "@testing-library/react";
import { useEffect } from "react";
import { describe, expect, it, vi } from "vitest";

import { useDebounce } from "@/hooks/use-debounce";
import { runStory } from "./run-story";

describe("portable story teardown", () => {
  it.each([false, true])(
    "unmounts and cancels production debounce timers after play failure=%s",
    async (playFails) => {
      const unmounted = vi.fn();
      const cleared = vi.spyOn(globalThis, "clearTimeout");
      const scheduled = vi.spyOn(globalThis, "setTimeout");
      const Fixture = () => {
        const value = useDebounce("pending", 60_000);
        useEffect(() => () => unmounted(), []);
        return <span>{value}</span>;
      };
      const Story = composeStory(
        {
          render: () => <Fixture />,
          play: async () => {
            if (playFails) throw new Error("play failed");
          },
        },
        { component: Fixture, title: "Test/Portable cleanup" }
      );
      const canvasElement = document.createElement("div");
      document.body.appendChild(canvasElement);
      try {
        if (playFails) {
          await expect(runStory(Story, canvasElement)).rejects.toThrow("play failed");
        } else {
          await runStory(Story, canvasElement);
        }
        expect(canvasElement).toHaveTextContent("pending");
        const timerIndex = scheduled.mock.calls.findIndex((call) => call[1] === 60_000);
        expect(timerIndex).toBeGreaterThanOrEqual(0);
        const timer = scheduled.mock.results[timerIndex].value;
        canvasElement.remove();
        expect(unmounted).not.toHaveBeenCalled();
        cleanup();
        expect(unmounted).toHaveBeenCalledOnce();
        expect(cleared).toHaveBeenCalledWith(timer);
      } finally {
        cleanup();
        scheduled.mock.calls.forEach((call, index) => {
          if (call[1] === 60_000) clearTimeout(scheduled.mock.results[index].value);
        });
        canvasElement.remove();
        vi.restoreAllMocks();
      }
    }
  );
});
