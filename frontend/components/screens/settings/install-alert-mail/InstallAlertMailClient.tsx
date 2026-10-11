"use client";
import * as React from "react";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { useMe } from "@/graphql/user/user.hooks";
import { InstallAlertMailPanel, type InstallMailEvent } from "./InstallAlertMailPanel";
import { useInstallAlertMail } from "./use-install-alert-mail";
function CurrentMail({ actor, org }: { actor: string; org: string }) {
  const [event, setEvent] = React.useState<InstallMailEvent>("deploy.failed");
  return <EventMail key={event} actor={actor} org={org} event={event} onEvent={setEvent} />;
}
function EventMail(p: {
  actor: string;
  org: string;
  event: InstallMailEvent;
  onEvent: (e: InstallMailEvent) => void;
}) {
  return <InstallAlertMailPanel {...useInstallAlertMail(p.actor, p.org, p.event, p.onEvent)} />;
}
export function InstallAlertMailClient() {
  const { user, loading: userLoading, error: userError } = useMe(),
    { org, loading: orgLoading, error: orgError } = useActiveOrg();
  const actor = !userLoading && !userError ? (user?.id ?? "") : "",
    organization = !orgLoading && !orgError ? (org?.id ?? "") : "";
  return <CurrentMail key={`${actor}:${organization}`} actor={actor} org={organization} />;
}
