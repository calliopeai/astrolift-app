export const metadata = {
  title: "Resources · Astrolift",
};

/**
 * The /resources subtree is retired (#916); every child page is now a
 * redirect into /documentation or /clusters, so no shared chrome remains.
 */
export default function ResourcesLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return children;
}
