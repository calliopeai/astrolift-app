import { ResourcesSubnav } from "./resources-subnav";

export const metadata = {
  title: "Resources · Astrolift",
};

/**
 * Shared chrome for the /resources subtree.
 *
 * The subnav (Docs / Manifest reference / Driver reference / Get help)
 * lives at the top of every child page so operators can jump between
 * sections without going back to the sidebar. Pages own their own
 * `PageShell` for title + description; the subnav is the only
 * cross-page chrome added by this layout.
 */
export default function ResourcesLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-1 flex-col">
      <ResourcesSubnav />
      <div className="flex-1">{children}</div>
    </div>
  );
}
