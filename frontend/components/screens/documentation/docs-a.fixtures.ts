import { BookOpenIcon, FileTextIcon, RocketIcon } from "lucide-react";

import type { ChangelogEntry } from "./ChangelogScreen";
import type { DocsNavSection } from "./DocsShell";
import type { DocSection } from "./DocumentationIndexScreen";

/** Hand-typed fixtures for the docs shell, index, and changelog (group docs-a). */

export const LONG =
  "An operator guide with a deliberately long title that keeps going to show how the layout wraps without overflowing";

export const LONG_WORD =
  "astrolift-platform-team-shared-production-workloads-us-west-2-reference-without-any-breaks";

export const LONG_NAV_SECTIONS: DocsNavSection[] = [
  {
    label: "Getting started with a very long section label",
    links: [
      { href: "/documentation/introduction", label: "Introduction", icon: BookOpenIcon },
      { href: "/documentation/long-guide", label: LONG, icon: RocketIcon },
      { href: "/documentation/long-word", label: LONG_WORD, icon: FileTextIcon },
    ],
  },
];

export const EMPTY_INDEX_SECTIONS: DocSection[] = [];

export const LONG_INDEX_SECTIONS: DocSection[] = [
  {
    label: "Operator guides with a very long section label that keeps going",
    cards: [
      {
        href: "/documentation/long-guide",
        title: LONG,
        description: `${LONG}. ${LONG}. ${LONG}.`,
        icon: RocketIcon,
      },
      {
        href: "https://astrolift.dev/reference/long-word/",
        title: LONG_WORD,
        description: LONG_WORD,
        icon: FileTextIcon,
        external: true,
      },
    ],
  },
];

export const EMPTY_CHANGELOG: ChangelogEntry[] = [];

export const LONG_CHANGELOG: ChangelogEntry[] = [
  {
    version: "10.120.3-rc.1+build.20260928.abcdef0",
    date: "2026-09-28",
    type: "feature",
    changes: [LONG, LONG_WORD, `${LONG}. ${LONG}.`],
  },
  {
    version: "10.120.2",
    date: "2026-09-20",
    type: "breaking",
    changes: ["An unrecognized release-note category uses a neutral outline badge."],
  },
];
