import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { INGRESS_AUTH } from "./fixtures";
import { IngressAuthView } from "./IngressAuth";

describe("ingress Apply progress", () => {
  it("shows progress during a save before reconciliation starts", () => {
    render(<IngressAuthView {...INGRESS_AUTH} busy reconciling={false} />);
    const apply = screen.getByRole("button", { name: /^Apply to cluster$/ });
    expect(apply).toBeDisabled();
    expect(apply.querySelector(".animate-spin")).not.toBeNull();
  });
});
