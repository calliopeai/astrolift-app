import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { ActivityIcon } from "lucide-react";
import * as React from "react";

import { useHeldRows } from "@/components/list/use-held-rows";
import { Panel } from "@/components/panel/Panel";
import { Button } from "@/components/ui/button";

import { Feed, type FeedProps } from "./Feed";
import { type FeedEvent, LONG_EVENTS, makeEvents } from "./fixtures";

/**
 * The feed primitive (list rule 5): scrolls in its own frame, loads older
 * items as the reader nears the end, Load older as the fallback.
 */
const meta: Meta = { title: "Feed/Feed", parameters: { layout: "padded" } };
export default meta;

type Story = StoryObj;

function EventLine({ event }: { event: FeedEvent }) {
  const time = new Date(event.at).toISOString().slice(11, 16);
  return (
    <div className="flex min-w-0 items-start gap-3 px-1">
      <time dateTime={event.at} className="text-muted-foreground shrink-0 font-mono text-xs">
        {time}
      </time>
      <p className="min-w-0 flex-1 text-sm [overflow-wrap:anywhere]">
        <span className="font-medium">{event.actor}</span>{" "}
        <span className="text-muted-foreground">{event.action}</span>{" "}
        <span className="font-mono text-xs">{event.target}</span>
      </p>
    </div>
  );
}

const BASE: FeedProps<FeedEvent> = {
  label: "Activity",
  items: [],
  keyOf: (e) => e.id,
  renderItem: (e) => <EventLine event={e} />,
};

const BY_DAY = { day: (e: FeedEvent) => e.at };

function InPanel({ children }: { children: React.ReactNode }) {
  return (
    <div className="max-w-2xl">
      <Panel title="Activity" icon={<ActivityIcon className="size-4" />}>
        {children}
      </Panel>
    </div>
  );
}

/** Pages of 15 on a fake cursor, 400ms each, four pages deep. */
function usePagedEvents(pageSize = 15, pages = 4) {
  const [items, setItems] = React.useState(() => makeEvents(pageSize));
  const [loadingMore, setLoadingMore] = React.useState(false);
  const hasMore = items.length < pageSize * pages;
  const onLoadMore = React.useCallback(() => {
    setLoadingMore(true);
    setTimeout(() => {
      setItems((prev) => [...prev, ...makeEvents(pageSize, prev.length)]);
      setLoadingMore(false);
    }, 400);
  }, [pageSize]);
  return { items, hasMore, loadingMore, onLoadMore };
}

/** Scroll toward the end: the next page loads before the reader gets there. */
export const LazyLoading: Story = {
  render: () => {
    function Demo() {
      const page = usePagedEvents();
      return (
        <InPanel>
          <Feed {...BASE} {...page} groupBy={BY_DAY} />
        </InPanel>
      );
    }
    return <Demo />;
  },
};

/** Live: new events wait behind the pill and the list holds still under the reader. */
export const LiveWithNewItems: Story = {
  render: () => {
    function Demo() {
      const [rows, setRows] = React.useState(() => makeEvents(12, 3));
      const [next, setNext] = React.useState(2);
      const held = useHeldRows(rows, (e) => e.id);
      const arrive = () => {
        setRows((r) => [...makeEvents(1, next, "live"), ...r]);
        setNext((n) => n - 1);
      };
      return (
        <div className="max-w-2xl space-y-3">
          <Button size="sm" variant="outline" onClick={arrive} disabled={next < 0}>
            Simulate a new event
          </Button>
          <Panel title="Activity" icon={<ActivityIcon className="size-4" />}>
            <Feed
              {...BASE}
              items={held.rows}
              newCount={held.newCount}
              onShowNew={held.reveal}
              hasMore
              onLoadMore={() => {}}
            />
          </Panel>
        </div>
      );
    }
    return <Demo />;
  },
};

/** Two new items already waiting. */
export const NewItemsWaiting: Story = {
  render: () => (
    <InPanel>
      <Feed {...BASE} items={makeEvents(10)} newCount={2} onShowNew={() => {}} hasMore />
    </InPanel>
  ),
};

/** No IntersectionObserver, or a fetch that failed once: the button carries the feed. */
export const LoadOlderFallback: Story = {
  render: () => (
    <InPanel>
      <Feed {...BASE} items={makeEvents(6)} hasMore onLoadMore={() => {}} />
    </InPanel>
  ),
};

export const LoadingOlder: Story = {
  render: () => (
    <InPanel>
      <Feed {...BASE} items={makeEvents(6)} hasMore loadingMore onLoadMore={() => {}} />
    </InPanel>
  ),
};

export const GroupedByDay: Story = {
  render: () => (
    <InPanel>
      <Feed {...BASE} items={makeEvents(40)} groupBy={BY_DAY} maxHeight="max-h-[32rem]" />
    </InPanel>
  ),
};

/** A caller key: a workflow run's rounds. */
export const GroupedByRound: Story = {
  render: () => (
    <InPanel>
      <Feed
        {...BASE}
        items={makeEvents(16)}
        groupBy={{ key: (e) => String(e.round), label: (k) => `Round ${k}` }}
        dense
      />
    </InPanel>
  ),
};

export const Dense: Story = {
  render: () => (
    <InPanel>
      <Feed {...BASE} items={makeEvents(20)} dense hasMore />
    </InPanel>
  ),
};

/** The end of the cursor. */
export const Full: Story = {
  render: () => (
    <InPanel>
      <Feed {...BASE} items={makeEvents(8)} groupBy={BY_DAY} />
    </InPanel>
  ),
};

export const Loading: Story = {
  render: () => (
    <InPanel>
      <Feed {...BASE} loading />
    </InPanel>
  ),
};

export const Empty: Story = {
  render: () => (
    <InPanel>
      <Feed
        {...BASE}
        empty={{
          icon: <ActivityIcon className="size-5" />,
          title: "No activity yet",
          description: "Deploys, config syncs and cluster changes show up here.",
        }}
      />
    </InPanel>
  ),
};

export const Error: Story = {
  render: () => (
    <InPanel>
      <Feed {...BASE} error="upstream timed out after 30s" onRetry={() => {}} />
    </InPanel>
  ),
};

/** The next page failed: items stay, the reason sits at the end, Load older retries. */
export const ErrorLoadingOlder: Story = {
  render: () => (
    <InPanel>
      <Feed
        {...BASE}
        items={makeEvents(6)}
        hasMore
        onLoadMore={() => {}}
        error="cursor expired: eyJvY2N1cnJlZF9hdCI6IjIwMjYtMDktMjhUMTI6MDA6MDBaIn0"
      />
    </InPanel>
  ),
};

/** A 64-char SHA, a 200-char ARN and an unbroken URL wrap inside the frame. */
export const LongLines: Story = {
  render: () => (
    <InPanel>
      <Feed {...BASE} items={LONG_EVENTS} groupBy={BY_DAY} />
    </InPanel>
  ),
};

export const Width768: Story = {
  render: () => {
    function Demo() {
      const page = usePagedEvents(10, 3);
      return (
        <div style={{ width: 768 }} className="overflow-hidden border p-4">
          <Panel title="Activity" icon={<ActivityIcon className="size-4" />}>
            <Feed {...BASE} {...page} items={[...LONG_EVENTS, ...page.items]} groupBy={BY_DAY} />
          </Panel>
        </div>
      );
    }
    return <Demo />;
  },
};
