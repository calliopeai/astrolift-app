import { expect, it } from "vitest";
import { isRouteEnabled, ROUTE_FLAGS } from "./route-flags";
it("enables real prompts and local history while keeping unrelated playground demos and forms parked", () => {
  for (const path of ["/playground", "/playground/history", "/playground/starred"])
    expect(isRouteEnabled(path), path).toBe(true);
  for (const path of [
    "/playground/topology",
    "/playground/topology/detail",
    "/playground/observability",
    "/forms",
    "/forms/new",
  ])
    expect(isRouteEnabled(path), path).toBe(false);
});
it("an enabled parent cannot accidentally enable a parked child, regardless of declaration order", () => {
  ROUTE_FLAGS["/test-real"] = true;
  ROUTE_FLAGS["/test-real/demo"] = false;
  try {
    expect(isRouteEnabled("/test-real/demo")).toBe(false);
    expect(isRouteEnabled("/test-real/demo/child")).toBe(false);
    expect(isRouteEnabled("/test-real/other")).toBe(true);
  } finally {
    delete ROUTE_FLAGS["/test-real"];
    delete ROUTE_FLAGS["/test-real/demo"];
  }
});
