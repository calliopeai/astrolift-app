import Link from "next/link";
import { useTranslations } from "next-intl";
import {
  AlertTriangleIcon,
  ArrowRightIcon,
  BookOpenIcon,
  ExternalLinkIcon,
  FileTextIcon,
  GitBranchIcon,
  GlobeIcon,
  GraduationCapIcon,
  KeyRoundIcon,
  ListTreeIcon,
  RocketIcon,
  ScaleIcon,
  ScrollIcon,
  ServerCogIcon,
  SettingsIcon,
  TerminalIcon,
  WebhookIcon,
  ZapIcon,
} from "lucide-react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

const PUBLIC_DOCS_URL = "https://astrolift.dev";

export interface DocCard {
  href: string;
  title: string;
  description: string;
  icon: typeof BookOpenIcon;
  /** Opens in a new tab with an external indicator (GitHub reference). */
  external?: boolean;
}

export interface DocSection {
  label: string;
  cards: DocCard[];
}

interface KeyedDocSection {
  /** Key under `docsNav.sections`. */
  labelKey: string;
  /** `key` names the card under `docsIndex.cards`. */
  cards: (Omit<DocCard, "title" | "description"> & { key: string })[];
}

const DOCS_INDEX: KeyedDocSection[] = [
  {
    labelKey: "gettingStarted",
    cards: [
      {
        href: "/documentation/introduction",
        key: "introduction",
        icon: BookOpenIcon,
      },
      {
        href: "/documentation/quickstart",
        key: "quickstart",
        icon: ZapIcon,
      },
      {
        href: "/documentation/get-started",
        key: "localDev",
        icon: RocketIcon,
      },
      {
        href: "/documentation/tutorials",
        key: "tutorials",
        icon: GraduationCapIcon,
      },
    ],
  },
  {
    labelKey: "operatorGuides",
    cards: [
      {
        href: "/documentation/cluster-prerequisites",
        key: "clusterPrerequisites",
        icon: ServerCogIcon,
      },
      {
        href: "/documentation/custom-domains",
        key: "customDomains",
        icon: GlobeIcon,
      },
      {
        href: "/documentation/source-providers",
        key: "sourceProviders",
        icon: GitBranchIcon,
      },
      {
        href: "/documentation/identity-providers",
        key: "identityProviders",
        icon: KeyRoundIcon,
      },
      {
        href: "/documentation/policies",
        key: "policies",
        icon: ScaleIcon,
      },
      {
        href: "/documentation/webhooks",
        key: "webhooks",
        icon: WebhookIcon,
      },
    ],
  },
  {
    labelKey: "runbooks",
    cards: [
      {
        href: "/documentation/runbooks",
        key: "runbooks",
        icon: AlertTriangleIcon,
      },
    ],
  },
  {
    labelKey: "reference",
    cards: [
      {
        href: "/documentation/configuration",
        key: "configuration",
        icon: SettingsIcon,
      },
      {
        href: "/documentation/webhook-events",
        key: "webhookEvents",
        icon: ListTreeIcon,
      },
      {
        href: "/documentation/changelog",
        key: "changelog",
        icon: ScrollIcon,
      },
    ],
  },
  {
    labelKey: "publicReference",
    cards: [
      {
        href: `${PUBLIC_DOCS_URL}/reference/astrolift-toml/`,
        key: "manifest",
        icon: FileTextIcon,
        external: true,
      },
      {
        href: `${PUBLIC_DOCS_URL}/reference/cli/`,
        key: "cli",
        icon: TerminalIcon,
        external: true,
      },
      {
        href: `${PUBLIC_DOCS_URL}/reference/api/`,
        key: "api",
        icon: KeyRoundIcon,
        external: true,
      },
      {
        href: `${PUBLIC_DOCS_URL}/reference/mcp/`,
        key: "mcp",
        icon: ZapIcon,
        external: true,
      },
      {
        href: `${PUBLIC_DOCS_URL}/reference/agent-packages/`,
        key: "agentPackages",
        icon: ListTreeIcon,
        external: true,
      },
      {
        href: `${PUBLIC_DOCS_URL}/reference/workflow-toml/`,
        key: "workflowToml",
        icon: GitBranchIcon,
        external: true,
      },
      {
        href: `${PUBLIC_DOCS_URL}/guides/plugin-sdk-cookbook/`,
        key: "pluginSdk",
        icon: ServerCogIcon,
        external: true,
      },
      {
        href: `${PUBLIC_DOCS_URL}/demo-scenarios/`,
        key: "demoScenarios",
        icon: RocketIcon,
        external: true,
      },
      {
        href: `${PUBLIC_DOCS_URL}/operators/install/`,
        key: "install",
        icon: GlobeIcon,
        external: true,
      },
    ],
  },
];

export interface DocumentationIndexScreenProps {
  /** Already-localized sections; defaults to the built-in index. */
  sections?: DocSection[];
}

/** The documentation landing page: a card grid per section. */
export function DocumentationIndexScreen({ sections }: DocumentationIndexScreenProps) {
  const t = useTranslations("docsIndex");
  const nav = useTranslations("docsNav");
  const resolved: DocSection[] =
    sections ??
    DOCS_INDEX.map(({ labelKey, cards }) => ({
      label: nav(`sections.${labelKey}`),
      cards: cards.map(({ key, ...card }) => ({
        ...card,
        title: t(`cards.${key}.title`),
        description: t(`cards.${key}.description`),
      })),
    }));
  return (
    <div className="flex flex-1 flex-col gap-8 p-6">
      <div>
        <h1 className="text-2xl font-semibold">{t("title")}</h1>
        <p className="text-muted-foreground mt-2 max-w-2xl text-sm">{t("intro")}</p>
      </div>

      {resolved.map((section) => (
        <section key={section.label} className="flex flex-col gap-3">
          <h2 className="text-muted-foreground text-xs font-semibold tracking-wider uppercase">
            {section.label}
          </h2>
          <div className="grid gap-4 sm:grid-cols-2">
            {section.cards.map((card) => {
              const Icon = card.icon;
              const body = (
                <Card className="group-hover:border-foreground/20 flex h-full flex-col transition-colors">
                  <CardHeader className="pb-2">
                    <div className="bg-muted text-foreground/80 inline-flex size-9 items-center justify-center rounded-md">
                      <Icon className="size-4" />
                    </div>
                    <CardTitle className="mt-3 text-base">{card.title}</CardTitle>
                  </CardHeader>
                  <CardContent className="flex flex-1 flex-col justify-between gap-3">
                    <CardDescription>{card.description}</CardDescription>
                    <span className="text-muted-foreground group-hover:text-foreground flex items-center gap-1 text-xs transition-colors">
                      {card.external ? t("readPublicDocs") : t("readGuide")}
                      {card.external ? (
                        <ExternalLinkIcon className="h-3 w-3" />
                      ) : (
                        <ArrowRightIcon className="h-3 w-3" />
                      )}
                    </span>
                  </CardContent>
                </Card>
              );
              return card.external ? (
                <a
                  key={card.href}
                  href={card.href}
                  target="_blank"
                  rel="noreferrer"
                  className="group"
                >
                  {body}
                </a>
              ) : (
                <Link key={card.href} href={card.href} className="group">
                  {body}
                </Link>
              );
            })}
          </div>
        </section>
      ))}
    </div>
  );
}
