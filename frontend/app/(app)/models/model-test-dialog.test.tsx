import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ModelTestDialog } from "./model-test-dialog";

const test = vi.fn();
vi.mock("@apollo/client/react", () => ({ useMutation: () => [test, { loading: false }] }));

function open() {
  render(<ModelTestDialog id="m1" name="qwen" />);
  fireEvent.click(screen.getByRole("button", { name: "Test" }));
}

describe("ModelTestDialog", () => {
  it("disables Send until a prompt is typed", () => {
    open();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Prompt"), { target: { value: "hello" } });
    expect(screen.getByRole("button", { name: "Send" })).toBeEnabled();
  });

  it("shows the reply, latency and token counts on success", async () => {
    test.mockResolvedValueOnce({
      data: {
        testModelEndpoint: {
          ok: true,
          errors: null,
          data: {
            status: "succeeded",
            reply: "Hello! How can I help?",
            latencyMs: 842,
            promptTokens: 5,
            completionTokens: 7,
            totalTokens: 12,
            error: "",
          },
        },
      },
    });
    open();
    fireEvent.change(screen.getByLabelText("Prompt"), { target: { value: "hello" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => expect(screen.getByText("Hello! How can I help?")).toBeInTheDocument());
    expect(test).toHaveBeenCalledWith({
      variables: { input: { managedServiceId: "m1", prompt: "hello" } },
    });
    expect(screen.getByText("842 ms")).toBeInTheDocument();
    expect(screen.getByText("5 + 7 = 12 tokens")).toBeInTheDocument();
  });

  it("shows a clear message when the cluster has no connected agent", async () => {
    test.mockResolvedValueOnce({
      data: {
        testModelEndpoint: {
          ok: false,
          errors: [
            {
              message:
                "this cluster has no connected agent; deploy the keep-alive agent from the cluster's settings and wait for it to report healthy before testing a model",
            },
          ],
          data: null,
        },
      },
    });
    open();
    fireEvent.change(screen.getByLabelText("Prompt"), { target: { value: "hello" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("this cluster has no connected agent")
    );
  });

  it("shows a clear message when the agent never responds in time", async () => {
    test.mockResolvedValueOnce({
      data: {
        testModelEndpoint: {
          ok: true,
          errors: null,
          data: {
            status: "timed_out",
            reply: "",
            latencyMs: null,
            promptTokens: null,
            completionTokens: null,
            totalTokens: null,
            error: "the cluster agent did not respond in time",
          },
        },
      },
    });
    open();
    fireEvent.change(screen.getByLabelText("Prompt"), { target: { value: "hello" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("no agent connected"));
  });

  it("clears the outcome when the dialog is closed and reopened", async () => {
    test.mockResolvedValueOnce({
      data: {
        testModelEndpoint: {
          ok: false,
          errors: [{ message: "some failure" }],
          data: null,
        },
      },
    });
    open();
    fireEvent.change(screen.getByLabelText("Prompt"), { target: { value: "hello" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());

    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    fireEvent.click(screen.getByRole("button", { name: "Test" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
