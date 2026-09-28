import Link from "next/link";
import {
  AlertTriangleIcon,
  BookOpenIcon,
  FileTextIcon,
  GitBranchIcon,
  GlobeIcon,
  GraduationCapIcon,
  KeyRoundIcon,
  LifeBuoyIcon,
  ListTreeIcon,
  PlugIcon,
  RocketIcon,
  ScaleIcon,
  ScrollIcon,
  ServerCogIcon,
  SettingsIcon,
  WebhookIcon,
  ZapIcon,
} from "lucide-react";

export interface DocsNavSection {
  label: string;
  links: { href: string; label: string; icon: typeof BookOpenIcon }[];
}

export const DOCS_NAV_SECTIONS: DocsNavSection[] = [
  {
    label: "Getting started",
    links: [
      { href: "/documentation/introduction", label: "Introduction", icon: BookOpenIcon },
      { href: "/documentation/quickstart", label: "Quickstart", icon: ZapIcon },
      { href: "/documentation/get-started", label: "Local dev setup", icon: RocketIcon },
      { href: "/documentation/tutorials", label: "Tutorials", icon: GraduationCapIcon },
    ],
  },
  {
    label: "Operator guides",
    links: [
      {
        href: "/documentation/cluster-prerequisites",
        label: "Cluster prerequisites",
        icon: ServerCogIcon,
      },
      { href: "/documentation/custom-domains", label: "Custom domains", icon: GlobeIcon },
      { href: "/documentation/source-providers", label: "Source providers", icon: GitBranchIcon },
      {
        href: "/documentation/identity-providers",
        label: "Identity providers",
        icon: KeyRoundIcon,
      },
      { href: "/documentation/policies", label: "ABAC policies", icon: ScaleIcon },
      { href: "/documentation/webhooks", label: "Webhooks", icon: WebhookIcon },
    ],
  },
  {
    label: "Runbooks",
    links: [{ href: "/documentation/runbooks", label: "All runbooks", icon: AlertTriangleIcon }],
  },
  {
    label: "Reference",
    links: [
      { href: "/documentation/configuration", label: "Configuration", icon: SettingsIcon },
      { href: "/documentation/manifest", label: "Manifest reference", icon: FileTextIcon },
      { href: "/documentation/drivers", label: "Driver reference", icon: PlugIcon },
      { href: "/documentation/webhook-events", label: "Webhook events", icon: ListTreeIcon },
      { href: "/documentation/changelog", label: "Changelog", icon: ScrollIcon },
      { href: "/documentation/help", label: "Get help", icon: LifeBuoyIcon },
    ],
  },
];

export interface DocsShellProps {
  /** Current path; the matching nav link (or its parent) is marked active. */
  pathname: string;
  children: React.ReactNode;
  sections?: DocsNavSection[];
}

/** The documentation shell: section nav on the left, the page on the right. */
export function DocsShell({ pathname, children, sections = DOCS_NAV_SECTIONS }: DocsShellProps) {
  return (
    <div className="flex flex-1">
      <aside className="hidden w-56 shrink-0 border-r lg:flex lg:flex-col">
        <div className="flex flex-col gap-5 p-4">
          {sections.map((section) => (
            <div key={section.label} className="flex flex-col gap-1">
              <p className="text-muted-foreground mb-1 px-2 text-xs font-semibold tracking-wider uppercase">
                {section.label}
              </p>
              {section.links.map(({ href, label, icon: Icon }) => {
                const isActive = pathname === href || pathname.startsWith(`${href}/`);
                return (
                  <Link
                    key={href}
                    href={href}
                    aria-current={isActive ? "page" : undefined}
                    className={`flex items-center gap-2.5 rounded-md px-2 py-2 text-sm transition-colors ${
                      isActive
                        ? "bg-accent text-accent-foreground font-medium"
                        : "hover:bg-accent hover:text-accent-foreground text-muted-foreground"
                    }`}
                  >
                    <Icon className="h-4 w-4 shrink-0" />
                    {label}
                  </Link>
                );
              })}
            </div>
          ))}
        </div>
      </aside>

      <div className="flex flex-1 flex-col">{children}</div>
    </div>
  );
}
