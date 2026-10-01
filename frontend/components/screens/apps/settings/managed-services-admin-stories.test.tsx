import { composeStories, setProjectAnnotations } from "@storybook/react";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import preview from "../../../../.storybook/preview";
import * as stories from "./ManagedServicesAdmin.stories";

setProjectAnnotations(preview);
const composed = composeStories(stories);
describe("Portable managed service admin stories", () => {
  it.each(Object.entries(composed))(
    "renders %s through the actual project decorators",
    (name, Story) => {
      render(<Story />);
      if (name === "Empty") expect(screen.queryByText("Managed service admin")).toBeNull();
      else if (name === "SpanishWidth768")
        expect(screen.getByText("Administración de servicios gestionados")).toBeInTheDocument();
      else expect(screen.getByText("Managed service admin")).toBeInTheDocument();
    }
  );
  it("declares loading as unknown and keeps cached error writes disabled", () => {
    const view = render(<composed.Loading />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading managed services");
    view.unmount();
    render(<composed.CachedReadError />);
    expect(screen.getByRole("alert")).toHaveTextContent("RAW_MANAGED_READ_DIAGNOSTIC");
    for (const button of screen.getAllByRole("button", { name: /^Edit|Re-provision/ }))
      expect(button).toBeDisabled();
    expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();
  });
  it("retains a later draft when an earlier save callback is accepted", async () => {
    let release!: (accepted: boolean) => void;
    const onSave = vi.fn(
      () =>
        new Promise<boolean>((resolve) => {
          release = resolve;
        })
    );
    render(<composed.WildcardTypedConfig onSave={onSave} />);
    await userEvent.click(screen.getByRole("button", { name: /^Edit/ }));
    fireEvent.change(screen.getByLabelText("capacity"), { target: { value: "8" } });
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
    fireEvent.change(screen.getByLabelText("capacity"), { target: { value: "12" } });
    await act(async () => release(true));
    expect(screen.getByLabelText("capacity")).toHaveValue("12");
    expect(onSave).toHaveBeenCalledTimes(1);
  });
  it("does not let an old accepted callback close a newly opened same-source review", async () => {
    let release!: (accepted: boolean) => void;
    const onSave = vi.fn(
      () =>
        new Promise<boolean>((resolve) => {
          release = resolve;
        })
    );
    render(<composed.WildcardTypedConfig onSave={onSave} />);
    await userEvent.click(screen.getByRole("button", { name: /^Edit/ }));
    fireEvent.change(screen.getByLabelText("capacity"), { target: { value: "8" } });
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await userEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" })
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await userEvent.click(screen.getByRole("button", { name: /^Edit/ }));
    fireEvent.change(screen.getByLabelText("capacity"), { target: { value: "16" } });
    await act(async () => release(true));
    expect(screen.getByLabelText("capacity")).toHaveValue("16");
  });
});
