import { renderWithIntl as render } from "@/test/render-with-intl";
import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ClusterStatusBody } from "./ClusterStatusScreen";
import { LIVE_CONNECTED, LIVE_LOADING, LIVE_NEVER_SEEN } from "./fixtures";

describe("observed cluster connectivity", () => {
  it("waits for connectivity without mounting provider queries or reporting no agent", () => {
    const mounted = vi.fn();
    function ProviderQuery() {
      mounted();
      return <div>Provider result</div>;
    }
    const props = {
      slug: "production",
      metrics: <ProviderQuery />,
      workloads: <ProviderQuery />,
      liveHealth: <ProviderQuery />,
      workflows: <ProviderQuery />,
      lifecycle: <div>Persisted lifecycle</div>,
    };
    const view = render(<ClusterStatusBody {...props} liveState={LIVE_LOADING} />);
    expect(mounted).not.toHaveBeenCalled();
    expect(screen.queryByText(/No agent/i)).not.toBeInTheDocument();
    expect(screen.getByText("Persisted lifecycle")).toBeVisible();

    view.rerender(
      <ClusterStatusBody
        {...props}
        liveState={{ ...LIVE_LOADING, loading: false, error: "Permission denied" }}
      />
    );
    expect(screen.getByText("Permission denied")).toBeVisible();
    expect(mounted).not.toHaveBeenCalled();
    expect(screen.queryByText(/No agent/i)).not.toBeInTheDocument();

    view.rerender(<ClusterStatusBody {...props} liveState={{ ...LIVE_LOADING, loading: false }} />);
    expect(screen.getByText("Cluster connection state unavailable")).toBeVisible();
    expect(mounted).not.toHaveBeenCalled();

    view.rerender(<ClusterStatusBody {...props} liveState={LIVE_CONNECTED} />);
    expect(mounted).toHaveBeenCalledTimes(4);
    expect(screen.getAllByText("Provider result")).toHaveLength(4);
    view.rerender(<ClusterStatusBody {...props} liveState={LIVE_NEVER_SEEN} />);
    expect(screen.queryByText("Provider result")).not.toBeInTheDocument();
    expect(screen.getAllByText(/No agent/i).length).toBeGreaterThan(0);
  });
});
