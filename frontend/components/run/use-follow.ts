"use client";

import * as React from "react";

/**
 * Follow the end of a growing scroll pane (spec 44 §5.5): the log follows by
 * default and stops following when the reader scrolls up. Scrolling back to
 * the end, the Follow toggle, or "Jump to latest" follow again.
 */

/** How close to the end still counts as "at the end", in px. */
export const END_SLACK = 24;

export interface ScrollMetrics {
  scrollTop: number;
  scrollHeight: number;
  clientHeight: number;
}

export function isAtEnd(m: ScrollMetrics): boolean {
  return m.scrollHeight - m.scrollTop - m.clientHeight <= END_SLACK;
}

/**
 * Following after one scroll event. At the end: follow. Moved up: stop.
 * Anything else (a scroll down short of the end): unchanged.
 */
export function followAfterScroll(
  following: boolean,
  previousTop: number,
  m: ScrollMetrics
): boolean {
  if (isAtEnd(m)) return true;
  if (m.scrollTop < previousTop) return false;
  return following;
}

export interface FollowController {
  following: boolean;
  setFollowing: (on: boolean) => void;
  /** Lines that arrived since following stopped. */
  unseen: number;
  onScroll: (e: React.UIEvent<HTMLElement>) => void;
  /** Scroll the pane to its end; the caller runs it in a layout effect while following. */
  pin: (pane: HTMLElement) => void;
}

/**
 * `count` is the number of lines in the pane. The pane pins itself:
 *
 *   const follow = useFollow(lines.length);
 *   React.useLayoutEffect(() => {
 *     if (follow.following && pane.current) follow.pin(pane.current);
 *   }, [follow.following, follow.pin, lines.length]);
 */
export function useFollow(count: number, initial = true): FollowController {
  const [following, setFollowingState] = React.useState(initial);
  const [countAtStop, setCountAtStop] = React.useState(count);
  const lastTop = React.useRef(0);

  const setFollowing = React.useCallback(
    (on: boolean) => {
      setFollowingState(on);
      if (!on) setCountAtStop(count);
    },
    [count]
  );

  const pin = React.useCallback((pane: HTMLElement) => {
    pane.scrollTop = pane.scrollHeight;
    lastTop.current = pane.scrollTop;
  }, []);

  const onScroll = (e: React.UIEvent<HTMLElement>) => {
    const el = e.currentTarget;
    const next = followAfterScroll(following, lastTop.current, el);
    lastTop.current = el.scrollTop;
    if (next !== following) setFollowing(next);
  };

  return {
    following,
    setFollowing,
    unseen: following ? 0 : Math.max(0, count - countAtStop),
    onScroll,
    pin,
  };
}
