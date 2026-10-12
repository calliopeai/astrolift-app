import Link from "next/link";
import { useTranslations } from "next-intl";
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

interface KeyedNavSection {
  /** Key under `docsNav.sections`; each link's `labelKey` is under `docsNav.links`. */
  labelKey: string;
  links: { href: string; labelKey: string; icon: typeof BookOpenIcon }[];
}

const DOCS_NAV: KeyedNavSection[] = [
  {
    labelKey: "gettingStarted",
    links: [
      { href: "/documentation/introduction", labelKey: "introduction", icon: BookOpenIcon },
      { href: "/documentation/quickstart", labelKey: "quickstart", icon: ZapIcon },
      { href: "/documentation/get-started", labelKey: "localDev", icon: RocketIcon },
      { href: "/documentation/tutorials", labelKey: "tutorials", icon: GraduationCapIcon },
    ],
  },
  {
    labelKey: "operatorGuides",
    links: [
      {
        href: "/documentation/cluster-prerequisites",
        labelKey: "clusterPrerequisites",
        icon: ServerCogIcon,
      },
      { href: "/documentation/custom-domains", labelKey: "customDomains", icon: GlobeIcon },
      { href: "/documentation/source-providers", labelKey: "sourceProviders", icon: GitBranchIcon },
      {
        href: "/documentation/identity-providers",
        labelKey: "identityProviders",
        icon: KeyRoundIcon,
      },
      { href: "/documentation/policies", labelKey: "policies", icon: ScaleIcon },
      { href: "/documentation/webhooks", labelKey: "webhooks", icon: WebhookIcon },
    ],
  },
  {
    labelKey: "runbooks",
    links: [{ href: "/documentation/runbooks", labelKey: "allRunbooks", icon: AlertTriangleIcon }],
  },
  {
    labelKey: "reference",
    links: [
      { href: "/documentation/configuration", labelKey: "configuration", icon: SettingsIcon },
      { href: "/documentation/manifest", labelKey: "manifest", icon: FileTextIcon },
      { href: "/documentation/drivers", labelKey: "drivers", icon: PlugIcon },
      { href: "/documentation/webhook-events", labelKey: "webhookEvents", icon: ListTreeIcon },
      { href: "/documentation/changelog", labelKey: "changelog", icon: ScrollIcon },
      { href: "/documentation/help", labelKey: "help", icon: LifeBuoyIcon },
    ],
  },
];

export interface DocsShellProps {
  /** Current path; the matching nav link (or its parent) is marked active. */
  pathname: string;
  children: React.ReactNode;
  /** Already-localized sections; defaults to the built-in nav. */
  sections?: DocsNavSection[];
}

/** The documentation shell: section nav on the left, the page on the right. */
export function DocsShell({ pathname, children, sections }: DocsShellProps) {
  const t = useTranslations("docsNav");
  const resolved: DocsNavSection[] =
    sections ??
    DOCS_NAV.map(({ labelKey, links }) => ({
      label: t(`sections.${labelKey}`),
      links: links.map(({ labelKey: key, ...link }) => ({ ...link, label: t(`links.${key}`) })),
    }));
  return (
    <div className="flex flex-1">
      <aside className="hidden w-56 shrink-0 border-r lg:flex lg:flex-col">
        <div className="flex flex-col gap-5 p-4">
          {resolved.map((section) => (
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
