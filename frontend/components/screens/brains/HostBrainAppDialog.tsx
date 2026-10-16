"use client";

import { useTranslations } from "next-intl";
import * as React from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export interface HostBrainAppValues {
  projectId: string;
  image: string;
  name: string;
  port: number;
}

export interface HostBrainAppProject {
  id: string;
  label: string;
}

const DEFAULT_PORT = 8080;

function HostBrainAppForm({
  brainSlug,
  projects,
  submitting,
  onSubmit,
  onDismiss,
}: {
  brainSlug: string;
  projects: HostBrainAppProject[];
  submitting: boolean;
  onSubmit: (values: HostBrainAppValues) => void;
  onDismiss: () => void;
}) {
  const t = useTranslations("brains.hostDialog");
  const [picked, setProjectId] = React.useState("");
  // The first project until the viewer picks one, including once they load.
  const projectId = picked || projects[0]?.id || "";
  const [image, setImage] = React.useState("");
  const [name, setName] = React.useState("");
  const [port, setPort] = React.useState(String(DEFAULT_PORT));
  const portNumber = Number(port);
  const portValid = Number.isInteger(portNumber) && portNumber >= 1 && portNumber <= 65535;
  const imageValid = image.trim() !== "" && !/\s/.test(image.trim());
  const valid = projects.some((p) => p.id === projectId) && imageValid && portValid;
  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault();
        if (valid)
          onSubmit({ projectId, image: image.trim(), name: name.trim(), port: portNumber });
      }}
    >
      <DialogHeader>
        <DialogTitle>{t("title", { slug: brainSlug })}</DialogTitle>
        <DialogDescription>{t("description")}</DialogDescription>
      </DialogHeader>
      <div className="space-y-2">
        <Label htmlFor="host-brain-project">{t("project")}</Label>
        <Select value={projectId} onValueChange={setProjectId}>
          <SelectTrigger id="host-brain-project" className="w-full">
            <SelectValue placeholder={projects.length ? t("selectProject") : t("noProjects")} />
          </SelectTrigger>
          <SelectContent>
            {projects.map((p) => (
              <SelectItem key={p.id} value={p.id}>
                <span className="font-mono text-xs">{p.label}</span>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="space-y-2">
        <Label htmlFor="host-brain-image">{t("image")}</Label>
        <Input
          id="host-brain-image"
          className="font-mono"
          placeholder="ghcr.io/acme/brain-chat:1.0"
          value={image}
          onChange={(e) => setImage(e.target.value)}
          aria-invalid={image !== "" && !imageValid}
        />
        <p className="text-muted-foreground text-xs">{t("imageHelp")}</p>
      </div>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-[1fr_8rem]">
        <div className="space-y-2">
          <Label htmlFor="host-brain-name">{t("name")}</Label>
          <Input
            id="host-brain-name"
            placeholder={t("namePlaceholder")}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="host-brain-port">{t("port")}</Label>
          <Input
            id="host-brain-port"
            inputMode="numeric"
            value={port}
            onChange={(e) => setPort(e.target.value)}
            aria-invalid={!portValid}
          />
        </div>
      </div>
      <p className="text-muted-foreground text-xs">{t("envHelp")}</p>
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onDismiss}>
          {t("cancel")}
        </Button>
        <Button type="submit" disabled={!valid || submitting}>
          {submitting ? t("creating") : t("create")}
        </Button>
      </DialogFooter>
    </form>
  );
}

/**
 * Host an app on a brain (#2243): the brain-app template, one public web
 * workload running an image the viewer names. Each deploy gives it the
 * brain's MCP URLs and a read token as BRAIN_* env. Pure view; the
 * mutation is the caller's.
 */
export function HostBrainAppDialog({
  brainSlug,
  projects,
  submitting,
  onSubmit,
  onDismiss,
}: {
  brainSlug: string | null;
  projects: HostBrainAppProject[];
  submitting: boolean;
  onSubmit: (values: HostBrainAppValues) => void;
  onDismiss: () => void;
}) {
  return (
    <Dialog open={brainSlug !== null} onOpenChange={(o) => !o && onDismiss()}>
      <DialogContent>
        {brainSlug !== null && (
          <HostBrainAppForm
            key={brainSlug}
            brainSlug={brainSlug}
            projects={projects}
            submitting={submitting}
            onSubmit={onSubmit}
            onDismiss={onDismiss}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}
