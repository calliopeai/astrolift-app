"use client";

import { AppWindowIcon, KeyRoundIcon, PlugIcon, TerminalIcon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";

import { Panel, PanelGrid } from "@/components/panel/Panel";
import { Button } from "@/components/ui/button";

import { mcpUrl } from "../brain-status-model";
import { brainHref } from "./brain-tabs-model";

export interface BrainConnectTabProps {
  slug: string;
  /** The environments with a URL: each serves MCP at `<url>/mcp`. */
  endpoints: { name: string; url: string }[];
  connecting: boolean;
  /** Issue the viewer a read token and show it once (the frame's Connect dialog). */
  onConnect: () => void;
  onOpenPortal?: (endpoint: string) => void;
}

/**
 * Connect (#2320, #2427): where an MCP client reaches this layer, and a
 * read token issued to the viewer for 15 minutes, shown once in the
 * Connect dialog as a client config. Opening this tab issues nothing; only
 * the button does. The CLI and hosted apps get theirs the same way. Pure.
 */
export function BrainConnectTab({ slug, endpoints, connecting, onConnect, onOpenPortal }: BrainConnectTabProps) {
  const t = useTranslations("brains.connectTab");
  const served = endpoints.length > 0;
  return (
    <PanelGrid>
      <Panel
        title={t("token")}
        icon={<KeyRoundIcon className="size-4" />}
        span={6}
        empty={
          served
            ? null
            : {
                icon: <PlugIcon className="size-5" />,
                title: t("notServed"),
                description: t("notServedDescription"),
              }
        }
      >
        <div className="flex min-w-0 flex-col gap-3">
          <p className="text-muted-foreground text-sm">{t("tokenDescription")}</p>
          <div>
            <Button onClick={onConnect} disabled={connecting}>
              <PlugIcon className="size-4" />
              {t("connect")}
            </Button>
          </div>
        </div>
      </Panel>

      <Panel
        title={t("endpoints")}
        icon={<PlugIcon className="size-4" />}
        span={6}
        empty={served ? null : { icon: <PlugIcon className="size-5" />, title: t("noEndpoints") }}
      >
        <ul className="flex min-w-0 flex-col gap-3">
          {endpoints.map((env) => (
            <li key={env.name} className="min-w-0">
              <p className="text-muted-foreground font-mono text-xs">{env.name}</p>
              <p className="bg-muted rounded-sm px-2 py-1 font-mono text-xs break-all">
                {mcpUrl(env.url)}
              </p>
              {onOpenPortal && (
                <Button disabled={connecting} onClick={() => onOpenPortal(env.url)}>
                  <AppWindowIcon className="size-4" />
                  {t("openPortal")}
                </Button>
              )}
            </li>
          ))}
        </ul>
      </Panel>

      <Panel title={t("cli")} icon={<TerminalIcon className="size-4" />} span={6}>
        <div className="flex min-w-0 flex-col gap-2">
          <p className="text-muted-foreground text-sm">{t("cliDescription")}</p>
          <pre className="bg-muted overflow-x-auto rounded-sm px-3 py-2 font-mono text-xs">
            {`astro brain connect ${slug}\nastro brain connect ${slug} --format claude`}
          </pre>
        </div>
      </Panel>

      <Panel title={t("apps")} icon={<AppWindowIcon className="size-4" />} span={6}>
        <div className="flex min-w-0 flex-col gap-2">
          <p className="text-muted-foreground text-sm">{t("appsDescription")}</p>
          <Link href={brainHref(slug, "apps")} className="text-primary text-sm hover:underline">
            {t("appsLink")}
          </Link>
        </div>
      </Panel>
    </PanelGrid>
  );
}
