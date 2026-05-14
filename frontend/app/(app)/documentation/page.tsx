import Link from "next/link";
import {
  ArrowRightIcon,
  BookOpenIcon,
  GitBranchIcon,
  GlobeIcon,
  GraduationCapIcon,
  KeyRoundIcon,
  RocketIcon,
  ScaleIcon,
  ScrollIcon,
  WebhookIcon,
} from "lucide-react";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

interface DocCard {
  href: string;
  title: string;
  description: string;
  icon: typeof BookOpenIcon;
}

const sections: { label: string; cards: DocCard[] }[] = [
  {
    label: "Getting started",
    cards: [
      {
        href: "/documentation/introduction",
        title: "Introduction",
        description: "What Astrolift is and the concepts you'll meet first.",
        icon: BookOpenIcon,
      },
      {
        href: "/documentation/get-started",
        title: "Get started",
        description: "Bring up the stack locally in under five minutes.",
        icon: RocketIcon,
      },
      {
        href: "/documentation/tutorials",
        title: "Tutorials",
        description:
          "Step-by-step walkthroughs for the most common build paths.",
        icon: GraduationCapIcon,
      },
    ],
  },
  {
    label: "Operator guides",
    cards: [
      {
        href: "/documentation/custom-domains",
        title: "Custom domains",
        description:
          "Bind app.acme.com to an app with auto-managed TLS via cert-manager.",
        icon: GlobeIcon,
      },
      {
        href: "/documentation/source-providers",
        title: "Source providers",
        description:
          "Connect GitHub or GitLab so a git push triggers a deploy.",
        icon: GitBranchIcon,
      },
      {
        href: "/documentation/identity-providers",
        title: "Identity providers",
        description:
          "Route sign-in through OIDC, SAML, Cognito, Auth0, Okta, Azure AD, Google, or GitHub.",
        icon: KeyRoundIcon,
      },
      {
        href: "/documentation/policies",
        title: "ABAC policies",
        description:
          "Author fine-grained allow/deny rules on top of role bindings.",
        icon: ScaleIcon,
      },
      {
        href: "/documentation/webhooks",
        title: "Webhooks",
        description:
          "Subscribe external systems to Astrolift events with HMAC-signed deliveries.",
        icon: WebhookIcon,
      },
    ],
  },
  {
    label: "Reference",
    cards: [
      {
        href: "/documentation/changelog",
        title: "Changelog",
        description: "New features and fixes, most recent first.",
        icon: ScrollIcon,
      },
    ],
  },
];

export const metadata = {
  title: "Documentation · Astrolift",
};

export default function DocumentationIndexPage() {
  return (
    <div className="flex flex-1 flex-col gap-8 p-6">
      <div>
        <h1 className="text-2xl font-semibold">Documentation</h1>
        <p className="text-muted-foreground mt-2 max-w-2xl text-sm">
          How Astrolift works, how to install it, and the operator runbooks
          for the surfaces that need configuration.
        </p>
      </div>

      {sections.map((section) => (
        <section key={section.label} className="flex flex-col gap-3">
          <h2 className="text-muted-foreground text-xs font-semibold tracking-wider uppercase">
            {section.label}
          </h2>
          <div className="grid gap-4 sm:grid-cols-2">
            {section.cards.map((card) => {
              const Icon = card.icon;
              return (
                <Link key={card.href} href={card.href} className="group">
                  <Card className="flex h-full flex-col transition-colors group-hover:border-foreground/20">
                    <CardHeader className="pb-2">
                      <div className="bg-muted text-foreground/80 inline-flex size-9 items-center justify-center rounded-md">
                        <Icon className="size-4" />
                      </div>
                      <CardTitle className="mt-3 text-base">
                        {card.title}
                      </CardTitle>
                    </CardHeader>
                    <CardContent className="flex flex-1 flex-col justify-between gap-3">
                      <CardDescription>{card.description}</CardDescription>
                      <span className="text-muted-foreground group-hover:text-foreground flex items-center gap-1 text-xs transition-colors">
                        Read guide
                        <ArrowRightIcon className="h-3 w-3" />
                      </span>
                    </CardContent>
                  </Card>
                </Link>
              );
            })}
          </div>
        </section>
      ))}
    </div>
  );
}
