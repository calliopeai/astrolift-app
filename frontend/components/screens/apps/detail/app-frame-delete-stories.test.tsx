import { composeStories, setProjectAnnotations } from "@storybook/react";
import { render, screen, waitFor, within } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import preview from "../../../../.storybook/preview";

import * as stories from "./AppFrame.stories";

setProjectAnnotations([preview]);
const composed = composeStories(stories);

describe("Portable AppFrame stories", () => {
  it.each(Object.entries(composed))(
    "renders %s with the actual project decorators",
    (_name, Story) => {
      const view = render(<Story />);
      expect(view.container).not.toBeEmptyDOMElement();
    }
  );

  it("preserves confirmation, literal slug and accepted close", async () => {
    const onDelete = vi.fn().mockResolvedValue(undefined);
    render(<composed.Overview onDelete={onDelete} />);
    await userEvent.click(screen.getByRole("button", { name: "More actions" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Delete app" }));
    const dialog = screen.getByRole("alertdialog");
    expect(dialog).toHaveTextContent("checkout");
    expect(onDelete).not.toHaveBeenCalled();
    await userEvent.click(within(dialog).getByRole("button", { name: "Delete app" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(onDelete).toHaveBeenCalledTimes(1);
  });

  it("keeps the delete action hidden in the existing permission story", async () => {
    render(<composed.WithoutDeployPermission />);
    await userEvent.click(screen.getByRole("button", { name: "More actions" }));
    expect(screen.queryByRole("menuitem", { name: "Delete app" })).toBeNull();
  });

  it("uses the Spanish story's translated confirmation at its declared width", async () => {
    const view = render(<composed.SpanishWidth768 />);
    expect(view.container.querySelector('[style="width: 768px;"]')).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Más acciones" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Eliminar aplicación" }));
    expect(screen.getByRole("alertdialog")).toHaveTextContent("checkout");
  });
});
