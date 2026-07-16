"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  AlertTriangleIcon,
  BoxIcon,
  ChevronRightIcon,
  ExternalLinkIcon,
  FileBoxIcon,
  PlusIcon,
  RocketIcon,
  Settings2Icon,
  ShieldIcon,
  Trash2Icon,
  UsersIcon,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
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
import { SOFT_DELETE_PROJECT } from "@/graphql/identity/identity.mutations";
import { LIST_MEMBERS, LIST_PROJECTS } from "@/graphql/identity/identity.queries";
import type {
  AstroliftMember,
  AstroliftProject,
  MutationResult,
} from "@/graphql/identity/identity.types";
import { LIST_APPS } from "@/graphql/registry/registry.queries";
import type { AstroliftRegisteredApp, ProvisioningStatus } from "@/graphql/registry/registry.types";
import { useFormatters } from "@/lib/i18n/formatters";

const statusDot: Record<ProvisioningStatus, "ok" | "warn" | "error" | "pending"> = {
  ready: "ok",
  pending: "warn",
  provisioning: "pending",
  failed: "error",
};

interface ProjectsResp {
  astroliftProjects: AstroliftProject[];
}
interface AppsResp {
  astroliftApps: AstroliftRegisteredApp[];
}
interface MembersResp {
  astroliftMembers: AstroliftMember[];
}

export function ProjectDetailClient({ slug }: { slug: string }) {
  const router = useRouter();
  const fmt = useFormatters();
  const [settingsOpen, setSettingsOpen] = React.useState(false);
  const [confirmOpen, setConfirmOpen] = React.useState(false);

  const projects = useQuery<ProjectsResp>(LIST_PROJECTS);
  const apps = useQuery<AppsResp>(LIST_APPS);
  const members = useQuery<MembersResp>(LIST_MEMBERS);

  const [softDelete, { loading: deleting }] = useMutation<{
    softDeleteProject: MutationResult<{ id: string; deleted: boolean }>;
  }>(SOFT_DELETE_PROJECT, {
    refetchQueries: [{ query: LIST_PROJECTS }],
    awaitRefetchQueries: true,
  });

  // Filter on the client. There's no singular astroliftProject(slug)
  // resolver yet, so we pull the list and pick — fine for the
  // typical org with O(10) projects. A focused project read will
  // matter for orgs with hundreds of projects (#TBD backend ticket).
  const project = projects.data?.astroliftProjects.find((p) => p.slug === slug);
  const projectApps = apps.data?.astroliftApps.filter((a) => a.projectSlug === slug) ?? [];

  // Members of this project: scopeKind=PROJECT and scopeId matches the
  // project's id. Org-wide members also inherit access — surface those
  // in a separate group below the explicit project members so it's
  // clear which are direct vs inherited.
  const directMembers =
    members.data?.astroliftMembers.filter(
      (m) => m.scopeKind === "PROJECT" && m.scopeId === project?.id
    ) ?? [];

  if (projects.loading && !project) {
    return (
      <PageShell title="Loading…">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-48 w-full" />
      </PageShell>
    );
  }

  if (!project) {
    return (
      <PageShell title="Project not found">
        <EmptyState
          icon={<AlertTriangleIcon className="size-5" />}
          title={`No project with slug ${slug}`}
          description="It may have been soft-deleted, or you may not have permission to read it."
          actionHref="/administration/projects"
          actionLabel="Back to projects"
        />
      </PageShell>
    );
  }

  async function handleDelete() {
    if (!project) return;
    const { data } = await softDelete({
      variables: { input: { id: project.id } },
    });
    if (data?.softDeleteProject.ok) {
      toast.success(`Deleted ${project.slug}`);
      router.push("/administration/projects");
    } else {
      throw new Error(data?.softDeleteProject.errors?.[0]?.message ?? "Delete failed");
    }
  }

  const activeApps = projectApps.filter((a) => a.provisioningStatus === "ready").length;
  const failingApps = projectApps.filter((a) => a.provisioningStatus === "failed").length;

  return (
    <PageShell
      title={
        <span className="flex items-center gap-2">
          <FileBoxIcon className="size-5" />
          {project.name}
        </span>
      }
      description={
        <nav aria-label="breadcrumb" className="text-muted-foreground text-sm">
          <ol className="flex flex-wrap items-center gap-1">
            <li>
              <Link href="/administration/teams" className="hover:text-foreground hover:underline">
                {project.organization.slug}
              </Link>
            </li>
            <li>
              <ChevronRightIcon className="size-3" />
            </li>
            <li>
              <Link href="/administration/teams" className="hover:text-foreground hover:underline">
                {project.team.slug}
              </Link>
            </li>
            <li>
              <ChevronRightIcon className="size-3" />
            </li>
            <li className="text-foreground font-mono text-xs">{project.slug}</li>
          </ol>
        </nav>
      }
      actions={
        <>
          <Button asChild variant="outline">
            <Link href="/apps/new">
              <PlusIcon className="size-4" /> New app
            </Link>
          </Button>
          <Button variant="outline" onClick={() => setSettingsOpen(true)}>
            <Settings2Icon className="size-4" /> Settings
          </Button>
        </>
      }
    >
      {/* ─── stats strip ───────────────────────────────────────────────── */}
      <div className="grid gap-4 sm:grid-cols-3">
        <StatTile
          icon={RocketIcon}
          label="Registered apps"
          value={projectApps.length}
          sub={failingApps > 0 ? `${failingApps} failing` : `${activeApps} ready`}
          tone={failingApps > 0 ? "warn" : undefined}
        />
        <StatTile
          icon={BoxIcon}
          label="Active deployments"
          value={activeApps}
          sub="apps with a healthy latest rollout"
        />
        <StatTile
          icon={UsersIcon}
          label="Direct members"
          value={directMembers.length}
          sub="users granted project-scope access"
        />
      </div>

      {/* ─── apps table ───────────────────────────────────────────────── */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <RocketIcon className="size-4" /> Apps in this project
          </CardTitle>
          <CardDescription>
            Registered apps associated with {project.team.slug}/{project.slug}. Click a row to open
            the app detail page.
          </CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          {apps.loading && projectApps.length === 0 ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : projectApps.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<RocketIcon className="size-5" />}
                title="No apps in this project"
                description="Register a new app and attach it to this project to see it here."
                actionHref="/apps/new"
                actionLabel="Register app"
              />
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead></TableHead>
                  <TableHead>App</TableHead>
                  <TableHead>Source</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Created</TableHead>
                  <TableHead className="text-right"></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {projectApps.map((a) => (
                  <TableRow key={a.id}>
                    <TableCell className="w-8">
                      <StatusDot status={statusDot[a.provisioningStatus]} />
                    </TableCell>
                    <TableCell>
                      <Link href={`/apps/${a.slug}`} className="hover:underline">
                        <div className="font-medium">{a.name}</div>
                        <div className="text-muted-foreground font-mono text-xs">{a.slug}</div>
                      </Link>
                    </TableCell>
                    <TableCell className="text-sm">
                      {a.sourceRepo ? (
                        <span className="font-mono text-xs">{a.sourceRepo}</span>
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </TableCell>
                    <TableCell>
                      <Badge variant="secondary" className="capitalize">
                        {a.provisioningStatus}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-muted-foreground text-sm">
                      {fmt.formatDate(a.createdAt)}
                    </TableCell>
                    <TableCell className="text-right">
                      <Button asChild size="sm" variant="ghost">
                        <Link href={`/apps/${a.slug}`}>
                          Open <ExternalLinkIcon className="size-3" />
                        </Link>
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* ─── members ───────────────────────────────────────────────────── */}
      <Card>
        <CardHeader className="flex flex-row items-start justify-between gap-3 space-y-0">
          <div>
            <CardTitle className="flex items-center gap-2 text-base">
              <ShieldIcon className="size-4" /> Project members
            </CardTitle>
            <CardDescription>
              Members with explicit access granted at the project scope. Org-wide and team-wide
              members are not listed here.
            </CardDescription>
          </div>
          <Can permission="org.manage_members">
            <Button asChild size="sm">
              <Link href="/administration/members">
                <PlusIcon className="size-4" /> Invite
              </Link>
            </Button>
          </Can>
        </CardHeader>
        <CardContent className="p-0">
          {members.loading && directMembers.length === 0 ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          ) : directMembers.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<UsersIcon className="size-5" />}
                title="No direct project members"
                description="Anyone with team or org-wide access already sees this project. Grant explicit project-scope access from the Members page."
                actionHref="/administration/members"
                actionLabel="Manage members"
              />
            </div>
          ) : (
            <ul className="divide-y">
              {directMembers.map((m) => (
                <li key={m.id} className="flex items-center justify-between px-6 py-3">
                  <div>
                    <div className="font-medium">{m.user.username || m.user.email}</div>
                    <div className="text-muted-foreground text-xs">
                      {m.user.email} · joined{" "}
                      {m.joinedAt ? fmt.formatDate(m.joinedAt) : "—"}
                    </div>
                  </div>
                  <Badge variant="outline" className="text-xs uppercase">
                    {m.scopeKind}
                  </Badge>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      {/* ─── settings sheet ────────────────────────────────────────────── */}
      <Sheet open={settingsOpen} onOpenChange={setSettingsOpen}>
        <SheetContent side="right" className="sm:max-w-md">
          <SheetHeader>
            <SheetTitle>Project settings</SheetTitle>
            <SheetDescription>
              Configure or remove {project.team.slug}/{project.slug}.
            </SheetDescription>
          </SheetHeader>
          <div className="space-y-6 px-4 py-4">
            <section className="space-y-2">
              <h3 className="text-sm font-medium">Identity</h3>
              <dl className="grid grid-cols-3 gap-x-3 gap-y-1.5 text-sm">
                <dt className="text-muted-foreground">Name</dt>
                <dd className="col-span-2">{project.name}</dd>
                <dt className="text-muted-foreground">Slug</dt>
                <dd className="col-span-2 font-mono text-xs">{project.slug}</dd>
                <dt className="text-muted-foreground">Team</dt>
                <dd className="col-span-2 font-mono text-xs">{project.team.slug}</dd>
                <dt className="text-muted-foreground">Created</dt>
                <dd className="col-span-2 text-xs">{fmt.formatDateTime(project.createdAt)}</dd>
              </dl>
              <p className="text-muted-foreground text-xs">
                Project rename ships when the backend mutation lands; the slug is intentionally
                immutable so downstream cost allocation stays stable across rename.
              </p>
            </section>
            <section className="space-y-2">
              <h3 className="text-destructive text-sm font-medium">Danger zone</h3>
              <p className="text-muted-foreground text-xs">
                Soft delete removes this project from listings. Apps attached to it remain visible
                until reassigned.
              </p>
              <Can permission="project.delete">
                <Button
                  variant="outline"
                  className="text-destructive border-destructive/40"
                  disabled={deleting}
                  onClick={() => setConfirmOpen(true)}
                >
                  <Trash2Icon className="size-4" />
                  Delete project
                </Button>
              </Can>
            </section>
          </div>
          <SheetFooter>
            <Button variant="outline" onClick={() => setSettingsOpen(false)}>
              Close
            </Button>
          </SheetFooter>
        </SheetContent>
      </Sheet>

      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title={`Delete project ${project.team.slug}/${project.slug}?`}
        description="Soft delete — apps remain visible until you reassign them. The slug becomes reclaimable."
        confirmLabel="Delete project"
        destructive
        onConfirm={handleDelete}
      />
    </PageShell>
  );
}

interface StatTileProps {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
  tone?: "warn" | "error";
}

function StatTile({ icon: Icon, label, value, sub, tone }: StatTileProps) {
  const accent =
    tone === "error"
      ? "border-danger-border bg-danger/5"
      : tone === "warn"
        ? "border-warning-border bg-warning/5"
        : "";
  return (
    <Card className={accent}>
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <CardTitle className="text-muted-foreground text-sm font-medium">{label}</CardTitle>
        <div className="bg-primary/10 text-primary rounded-md p-1.5">
          <Icon className="h-4 w-4" />
        </div>
      </CardHeader>
      <CardContent>
        <p className="text-2xl font-bold tabular-nums">{value}</p>
        {sub != null && <p className="text-muted-foreground mt-1 text-xs">{sub}</p>}
      </CardContent>
    </Card>
  );
}
