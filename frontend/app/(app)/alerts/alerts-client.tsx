"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  BellIcon,
  BellOffIcon,
  CheckIcon,
  PlusIcon,
  Trash2Icon,
} from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import type { MutationResult } from "@/graphql/identity/identity.types";
import {
  ACKNOWLEDGE_ALERT_EVENT,
  CREATE_ALERT_RULE,
  DELETE_ALERT_RULE,
  LIST_ALERT_EVENTS,
  LIST_ALERT_RULES,
} from "@/graphql/operations/alerts.queries";

interface AlertRule {
  id: string;
  name: string;
  target: string;
  targetId: string;
  severity: string;
  predicate: Record<string, unknown>;
  notifyChannels: Record<string, unknown>;
  isActive: boolean;
  organizationSlug: string;
  createdAt: string;
  updatedAt: string;
}

interface AlertEvent {
  id: string;
  ruleId: string;
  severity: string;
  firedAt: string;
  resolvedAt?: string | null;
  acknowledgedAt?: string | null;
  summary: string;
  detail: Record<string, unknown>;
}

interface RulesResp {
  astroliftAlertRules: AlertRule[];
}
interface EventsResp {
  astroliftAlertEvents: AlertEvent[];
}

const SEVERITY_TONE: Record<string, "ok" | "warn" | "error" | "muted"> = {
  info: "ok",
  warn: "warn",
  warning: "warn",
  critical: "error",
  error: "error",
};

const TARGETS = ["app", "env", "workload", "global"];
const SEVERITIES = ["info", "warn", "critical"];

export function AlertsClient() {
  const [createOpen, setCreateOpen] = React.useState(false);
  const rules = useQuery<RulesResp>(LIST_ALERT_RULES, {
    variables: { activeOnly: false },
    fetchPolicy: "cache-and-network",
  });
  const events = useQuery<EventsResp>(LIST_ALERT_EVENTS, {
    variables: { unresolvedOnly: false, limit: 100 },
    fetchPolicy: "cache-and-network",
    pollInterval: 30000,
  });

  const refetch = [
    { query: LIST_ALERT_RULES, variables: { activeOnly: false } },
    {
      query: LIST_ALERT_EVENTS,
      variables: { unresolvedOnly: false, limit: 100 },
    },
  ];

  const [createRule, createState] = useMutation<{
    createAlertRule: MutationResult<AlertRule>;
  }>(CREATE_ALERT_RULE, { refetchQueries: refetch, awaitRefetchQueries: true });
  const [deleteRule, deleteState] = useMutation<{
    deleteAlertRule: MutationResult<{ id: string; deleted: boolean }>;
  }>(DELETE_ALERT_RULE, { refetchQueries: refetch, awaitRefetchQueries: true });
  const [ackEvent, ackState] = useMutation<{
    acknowledgeAlertEvent: MutationResult<AlertEvent>;
  }>(ACKNOWLEDGE_ALERT_EVENT, {
    refetchQueries: refetch,
    awaitRefetchQueries: true,
  });

  const busy = createState.loading || deleteState.loading || ackState.loading;
  const ruleList = rules.data?.astroliftAlertRules ?? [];
  const eventList = events.data?.astroliftAlertEvents ?? [];
  const unresolvedEvents = eventList.filter((e) => !e.resolvedAt);

  async function handleDelete(r: AlertRule) {
    if (!confirm(`Delete rule "${r.name}"? Events already fired stay in the log.`)) {
      return;
    }
    const { data } = await deleteRule({ variables: { input: { id: r.id } } });
    if (data?.deleteAlertRule.ok) {
      toast.success(`Deleted ${r.name}`);
    } else {
      toast.error(data?.deleteAlertRule.errors?.[0]?.message ?? "Delete failed");
    }
  }

  async function handleAcknowledge(e: AlertEvent) {
    const { data } = await ackEvent({ variables: { input: { id: e.id } } });
    if (!data?.acknowledgeAlertEvent.ok) {
      toast.error(
        data?.acknowledgeAlertEvent.errors?.[0]?.message ?? "Ack failed",
      );
    }
  }

  return (
    <PageShell
      title="Alerts"
      description="Org-wide alert rules + the firing events they produce. Rules target apps, environments, workloads, or the org globally; firing events surface in the bottom panel."
      actions={
        <Can permission="org.update">
          <Button onClick={() => setCreateOpen(true)}>
            <PlusIcon className="size-4" />
            New rule
          </Button>
        </Can>
      }
    >
      <div className="grid gap-4 lg:grid-cols-3">
        <Card>
          <CardContent className="p-4">
            <p className="text-muted-foreground text-xs uppercase tracking-wide">
              Rules
            </p>
            <p className="mt-1 text-2xl font-bold">{ruleList.length}</p>
            <p className="text-muted-foreground text-xs">
              {ruleList.filter((r) => r.isActive).length} active
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <p className="text-muted-foreground text-xs uppercase tracking-wide">
              Unresolved
            </p>
            <p className="mt-1 text-2xl font-bold text-destructive">
              {unresolvedEvents.length}
            </p>
            <p className="text-muted-foreground text-xs">in current window</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <p className="text-muted-foreground text-xs uppercase tracking-wide">
              Total events
            </p>
            <p className="mt-1 text-2xl font-bold">{eventList.length}</p>
            <p className="text-muted-foreground text-xs">latest 100</p>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardContent className="p-0">
          <div className="border-b p-4">
            <h2 className="font-medium">Rules</h2>
            <p className="text-muted-foreground text-xs">
              Predicates fire events when matching telemetry crosses a threshold.
            </p>
          </div>
          {rules.loading && ruleList.length === 0 ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : ruleList.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<BellIcon className="size-5" />}
                title="No alert rules yet"
                description="Create a rule to start surfacing platform events as alerts."
              />
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead></TableHead>
                  <TableHead>Name</TableHead>
                  <TableHead>Target</TableHead>
                  <TableHead>Severity</TableHead>
                  <TableHead>Created</TableHead>
                  <TableHead className="text-right"></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {ruleList.map((r) => (
                  <TableRow key={r.id}>
                    <TableCell className="w-8">
                      {r.isActive ? (
                        <BellIcon className="size-4 text-emerald-500" />
                      ) : (
                        <BellOffIcon className="size-4 text-muted-foreground" />
                      )}
                    </TableCell>
                    <TableCell className="font-medium">{r.name}</TableCell>
                    <TableCell>
                      <Badge variant="outline">{r.target}</Badge>
                      {r.targetId && (
                        <span className="text-muted-foreground ml-2 font-mono text-[11px]">
                          {r.targetId}
                        </span>
                      )}
                    </TableCell>
                    <TableCell>
                      <Badge variant="secondary" className="capitalize">
                        {r.severity}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-muted-foreground text-xs">
                      {new Date(r.createdAt).toLocaleDateString()}
                    </TableCell>
                    <TableCell className="text-right">
                      <Can permission="org.update">
                        <Button
                          variant="ghost"
                          size="icon"
                          className="size-8"
                          onClick={() => handleDelete(r)}
                          disabled={busy}
                        >
                          <Trash2Icon className="size-4" />
                          <span className="sr-only">Delete</span>
                        </Button>
                      </Can>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardContent className="p-0">
          <div className="border-b p-4">
            <h2 className="font-medium">Recent events</h2>
            <p className="text-muted-foreground text-xs">
              Firing instances from the rules above. Acknowledge to silence
              while the underlying issue is being worked.
            </p>
          </div>
          {events.loading && eventList.length === 0 ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : eventList.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<BellIcon className="size-5" />}
                title="No events yet"
                description="Events appear here when an active rule's predicate matches incoming telemetry."
              />
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead></TableHead>
                  <TableHead>Summary</TableHead>
                  <TableHead>Severity</TableHead>
                  <TableHead>Fired</TableHead>
                  <TableHead>State</TableHead>
                  <TableHead className="text-right"></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {eventList.map((e) => (
                  <TableRow key={e.id}>
                    <TableCell className="w-8">
                      <StatusDot status={SEVERITY_TONE[e.severity] ?? "muted"} />
                    </TableCell>
                    <TableCell className="max-w-md truncate">
                      {e.summary}
                    </TableCell>
                    <TableCell>
                      <Badge variant="secondary" className="capitalize">
                        {e.severity}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-muted-foreground text-xs">
                      {new Date(e.firedAt).toLocaleString()}
                    </TableCell>
                    <TableCell>
                      {e.resolvedAt ? (
                        <Badge variant="outline">resolved</Badge>
                      ) : e.acknowledgedAt ? (
                        <Badge variant="secondary">acknowledged</Badge>
                      ) : (
                        <Badge variant="destructive">firing</Badge>
                      )}
                    </TableCell>
                    <TableCell className="text-right">
                      {!e.resolvedAt && !e.acknowledgedAt && (
                        <Can permission="org.update">
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => handleAcknowledge(e)}
                            disabled={busy}
                          >
                            <CheckIcon className="size-3.5" />
                            Ack
                          </Button>
                        </Can>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <CreateRuleSheet
        open={createOpen}
        onOpenChange={setCreateOpen}
        onSubmit={async (input) => {
          const { data } = await createRule({ variables: { input } });
          if (data?.createAlertRule.ok) {
            toast.success(`Created ${input.name}`);
            setCreateOpen(false);
            return true;
          }
          toast.error(
            data?.createAlertRule.errors?.[0]?.message ?? "Create failed",
          );
          return false;
        }}
        busy={busy}
      />
    </PageShell>
  );
}

function CreateRuleSheet({
  open,
  onOpenChange,
  onSubmit,
  busy,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (input: {
    name: string;
    target: string;
    targetId: string | null;
    severity: string;
    predicate: Record<string, unknown> | null;
    notifyChannels: Record<string, unknown> | null;
  }) => Promise<boolean>;
  busy: boolean;
}) {
  const [name, setName] = React.useState("");
  const [target, setTarget] = React.useState("global");
  const [targetId, setTargetId] = React.useState("");
  const [severity, setSeverity] = React.useState("warn");
  const [predicateText, setPredicateText] = React.useState(
    JSON.stringify({ event: "deployment.failed" }, null, 2),
  );
  const [channelsText, setChannelsText] = React.useState(
    JSON.stringify({ slack: "#oncall" }, null, 2),
  );
  const [predicateError, setPredicateError] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (!open) {
      setName("");
      setTarget("global");
      setTargetId("");
      setSeverity("warn");
      setPredicateText(JSON.stringify({ event: "deployment.failed" }, null, 2));
      setChannelsText(JSON.stringify({ slack: "#oncall" }, null, 2));
      setPredicateError(null);
    }
  }, [open]);

  const isGlobal = target === "global";

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex flex-col sm:max-w-xl">
        <SheetHeader>
          <SheetTitle>New alert rule</SheetTitle>
          <SheetDescription>
            Predicate matches against the platform event stream. Notify channels
            describe where firing events go (Slack, email, webhook).
          </SheetDescription>
        </SheetHeader>
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            if (!name.trim()) return;
            let predicate: Record<string, unknown> | null = null;
            let channels: Record<string, unknown> | null = null;
            try {
              predicate = predicateText.trim() ? JSON.parse(predicateText) : null;
              channels = channelsText.trim() ? JSON.parse(channelsText) : null;
              setPredicateError(null);
            } catch (err) {
              setPredicateError(String(err));
              return;
            }
            await onSubmit({
              name: name.trim(),
              target,
              targetId: isGlobal ? null : targetId.trim() || null,
              severity,
              predicate,
              notifyChannels: channels,
            });
          }}
          className="flex flex-1 flex-col gap-4 overflow-auto px-4 pb-4"
        >
          <div className="space-y-2">
            <Label htmlFor="ar-name">Name</Label>
            <Input
              id="ar-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="failed-deploy"
              autoFocus
              required
              spellCheck={false}
              className="font-mono"
            />
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="ar-target">Target</Label>
              <Select value={target} onValueChange={setTarget}>
                <SelectTrigger id="ar-target">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {TARGETS.map((t) => (
                    <SelectItem key={t} value={t}>
                      {t}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="ar-severity">Severity</Label>
              <Select value={severity} onValueChange={setSeverity}>
                <SelectTrigger id="ar-severity">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {SEVERITIES.map((s) => (
                    <SelectItem key={s} value={s}>
                      {s}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="space-y-2">
            <Label htmlFor="ar-target-id">Target ID</Label>
            <Input
              id="ar-target-id"
              value={targetId}
              onChange={(e) => setTargetId(e.target.value)}
              placeholder={isGlobal ? "(blank for global)" : "app slug, env name, or workload slug"}
              disabled={isGlobal}
              spellCheck={false}
              className="font-mono"
            />
            <p className="text-muted-foreground text-xs">
              {isGlobal
                ? "Global rules ignore targetId — backend rejects non-empty values."
                : "App slug for app target; env name for env target; etc."}
            </p>
          </div>
          <div className="space-y-2">
            <Label htmlFor="ar-predicate">Predicate (JSON)</Label>
            <Textarea
              id="ar-predicate"
              value={predicateText}
              onChange={(e) => setPredicateText(e.target.value)}
              rows={6}
              spellCheck={false}
              className="font-mono text-xs"
            />
            {predicateError && (
              <p className="text-destructive text-xs">{predicateError}</p>
            )}
          </div>
          <div className="space-y-2">
            <Label htmlFor="ar-channels">Notify channels (JSON)</Label>
            <Textarea
              id="ar-channels"
              value={channelsText}
              onChange={(e) => setChannelsText(e.target.value)}
              rows={4}
              spellCheck={false}
              className="font-mono text-xs"
            />
          </div>
          <SheetFooter className="flex-row justify-end gap-2 px-0">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={busy || !name.trim()}>
              {busy ? "Creating…" : "Create rule"}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}
