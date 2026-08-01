import { act, render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { usePodTarget } from "./use-pod-target";

/**
 * #1250: PodExpander deep-linked `?pod=`/`?container=` and no destination
 * ever read them, so expanding a misbehaving pod and clicking through landed
 * on whichever pod sorted first. Invisible on a single-replica app; on
 * anything scaled out it silently targeted the wrong replica — which for the
 * shell means an interactive session on a healthy pod while triaging a sick
 * one.
 *
 * A pod name is short-lived and a link outlives the pod it names, so the URL
 * has to behave as a default rather than a pin. These pin both halves.
 */

const state = vi.hoisted(() => ({
  search: "",
  pods: [] as { name: string; status: string; workload: string | null; containers: string[] }[],
}));

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(state.search),
}));

vi.mock("@apollo/client/react", () => ({
  useQuery: () => ({
    data: {
      astroliftAppPods: state.pods.map((p) => ({
        name: p.name,
        status: p.status,
        workload: p.workload,
        containerStatuses: p.containers.map((c) => ({ name: c })),
      })),
    },
    loading: false,
  }),
}));

vi.mock("@/graphql/lifecycle/lifecycle.queries", () => ({ LIST_APP_PODS: {} }));

type Harness = ReturnType<typeof usePodTarget>;

function renderHook(): { current: Harness } {
  const ref = { current: null as unknown as Harness };
  function Probe() {
    ref.current = usePodTarget("acme");
    return null;
  }
  render(<Probe />);
  return ref;
}

const POD_A = { name: "web-aaa", status: "Running", workload: "web", containers: ["web", "envoy"] };
const POD_B = { name: "web-bbb", status: "Running", workload: "web", containers: ["web", "envoy"] };

describe("usePodTarget deep link (#1250)", () => {
  it("selects the pod named in the URL rather than the first one", () => {
    state.pods = [POD_A, POD_B];
    state.search = "?pod=web-bbb";

    const hook = renderHook();

    // Without the fix this returned web-aaa — the first Running pod.
    expect(hook.current.selectedPod).toBe("web-bbb");
  });

  it("selects the container named in the URL", () => {
    state.pods = [POD_A];
    state.search = "?pod=web-aaa&container=envoy";

    const hook = renderHook();

    // `envoy` is a known sidecar, so the heuristic default would skip it.
    // An explicit ask has to win over the heuristic.
    expect(hook.current.selectedContainer).toBe("envoy");
  });

  it("falls back to the default when the linked pod is gone", () => {
    // Pod names are short-lived; a link pasted into an incident thread
    // outlives the pod it names. Stranding the surface on a dead name would
    // be worse than ignoring the parameter.
    state.pods = [POD_A, POD_B];
    state.search = "?pod=web-deleted-yesterday";

    const hook = renderHook();

    expect(hook.current.selectedPod).toBe("web-aaa");
  });

  it("falls back to the container heuristic when the linked container is gone", () => {
    state.pods = [POD_A];
    state.search = "?pod=web-aaa&container=removed-sidecar";

    const hook = renderHook();

    expect(hook.current.selectedContainer).toBe("web");
  });

  it("lets an explicit pick override the URL", () => {
    // The URL is a default, not a pin: once the operator touches the picker
    // their choice has to stick, even though the query string still names
    // the original pod.
    state.pods = [POD_A, POD_B];
    state.search = "?pod=web-bbb";

    const hook = renderHook();
    expect(hook.current.selectedPod).toBe("web-bbb");

    act(() => hook.current.setPickedPod("web-aaa"));
    expect(hook.current.selectedPod).toBe("web-aaa");
  });

  it("still picks a sensible default with no query string at all", () => {
    state.pods = [
      { name: "web-pending", status: "Pending", workload: "web", containers: ["web"] },
      POD_B,
    ];
    state.search = "";

    const hook = renderHook();

    // Prefers a Running pod over the first row.
    expect(hook.current.selectedPod).toBe("web-bbb");
    expect(hook.current.selectedContainer).toBe("web");
  });
});
