import * as React from "react";

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ScopePicker } from "./scope-picker";

/**
 * The token picker reads its scopes from the server (#2120) and explains
 * each one, so an operator reaches for the narrow scope instead of `admin`.
 */

const catalog = {
  scopes: [
    {
      value: "read:clusters",
      label: "Read clusters",
      surface: "clusters",
      description: "List clusters.",
      sensitive: false,
      permissions: ["provider_plugin.read"],
      available: true,
      unavailableReason: "",
    },
    {
      value: "write:clusters",
      label: "Update clusters",
      surface: "clusters",
      description: "Change a cluster's settings.",
      sensitive: false,
      permissions: ["cluster.update"],
      available: true,
      unavailableReason: "",
    },
    {
      value: "manage:clusters",
      label: "Operate clusters",
      surface: "clusters",
      description: "Run a cluster's recipe.",
      sensitive: false,
      permissions: ["cluster.manage"],
      available: false,
      unavailableReason: "Your roles grant none of what this scope unlocks.",
    },
    {
      value: "admin",
      label: "Admin",
      surface: "administration",
      description: "Everything.",
      sensitive: true,
      permissions: ["cluster.update", "cluster.manage"],
      available: true,
      unavailableReason: "",
    },
  ],
  presets: [
    {
      key: "cluster_operator",
      label: "Cluster operator",
      scopes: ["read:clusters", "write:clusters", "manage:clusters"],
    },
  ],
};

vi.mock("@apollo/client/react", () => ({
  useQuery: () => ({
    data: { astroliftApiTokenScopeCatalog: catalog },
    loading: false,
    error: undefined,
  }),
}));

function Harness({ initial = [] as string[] }) {
  const [value, setValue] = React.useState<string[]>(initial);
  return (
    <>
      <ScopePicker value={value} onChange={setValue} />
      <output data-testid="value">{value.join(",")}</output>
    </>
  );
}

describe("ScopePicker (#2120)", () => {
  it("groups scopes by surface and says what each unlocks", () => {
    render(<Harness />);

    expect(screen.getByRole("group", { name: "Clusters" })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Administration" })).toBeInTheDocument();
    expect(screen.getByText("Change a cluster's settings.")).toBeInTheDocument();
    expect(screen.getAllByText(/Unlocks 1 permission$/).length).toBeGreaterThan(0);
  });

  it("disables a scope the caller's roles never grant, with the reason", () => {
    render(<Harness />);

    expect(screen.getByRole("checkbox", { name: /Operate clusters/ })).toBeDisabled();
    expect(screen.getByText("Your roles grant none of what this scope unlocks.")).toBeInTheDocument();
  });

  it("a preset selects only the scopes the caller can use", () => {
    render(<Harness />);

    fireEvent.click(screen.getByRole("button", { name: "Cluster operator" }));

    expect(screen.getByTestId("value").textContent).toBe("read:clusters,write:clusters");
  });

  it("warns plainly when admin is selected", () => {
    render(<Harness />);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("checkbox", { name: /Admin/ }));

    expect(screen.getByRole("alert").textContent).toMatch(/everything its owner can/);
  });

  it("keeps an unusable scope removable once selected", () => {
    render(<Harness initial={["manage:clusters"]} />);

    const box = screen.getByRole("checkbox", { name: /Operate clusters/ });
    expect(box).not.toBeDisabled();
    fireEvent.click(box);
    expect(screen.getByTestId("value").textContent).toBe("");
  });
});
