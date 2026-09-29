"use client";

import * as React from "react";

import { SCOPE_NOUN } from "@/components/access/access-model";
import { SettingsPage, SettingsSection } from "@/components/settings/SettingsPage";
import { DefinitionList } from "@/components/ui/definition-list";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import type { AstroliftRole } from "@/graphql/identity/identity.types";

export interface RoleSettingsTabProps {
  role: AstroliftRole;
  canManage: boolean;
  saving: boolean;
  /** Null when saved, or the reason, shown beside Save. */
  onSave: (settings: { name: string; description: string }) => Promise<string | null>;
}

/**
 * A role's Settings tab (spec 44 §5.3): name and description, saved on their
 * own. The slug and the level a role binds at are fixed once it exists. A
 * built-in role's fields are shown and cannot change; a viewer without
 * `org.manage_members` sees them disabled with the permission named. The
 * caller keys it by the saved values, so a save starts it afresh. Pure.
 */
export function RoleSettingsTab({ role, canManage, saving, onSave }: RoleSettingsTabProps) {
  const [name, setName] = React.useState(role.name);
  const [description, setDescription] = React.useState(role.description ?? "");
  const [error, setError] = React.useState<string | null>(null);

  const locked = role.isSystem;
  const dirty = name.trim() !== role.name || description.trim() !== (role.description ?? "");

  return (
    <SettingsPage
      readOnly={canManage ? undefined : { permission: "org.manage_members" }}
      sections={[
        {
          id: "general",
          title: "Name and description",
          content: (
            <SettingsSection
              title="Name and description"
              description={
                locked
                  ? "Built-in roles keep the name and description they ship with."
                  : "What people see when they grant this role."
              }
              dirty={!locked && dirty && Boolean(name.trim())}
              saving={saving}
              error={error}
              onSave={
                locked ? undefined : async () => setError(await onSave({ name, description }))
              }
              onCancel={() => {
                setName(role.name);
                setDescription(role.description ?? "");
                setError(null);
              }}
            >
              <div className="flex min-w-0 flex-col gap-2">
                <Label htmlFor="role-name">Name</Label>
                <Input
                  id="role-name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  disabled={locked}
                  required
                />
              </div>
              <div className="flex min-w-0 flex-col gap-2">
                <Label htmlFor="role-description">Description</Label>
                <Textarea
                  id="role-description"
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="What this role is for, in one sentence."
                  rows={3}
                  disabled={locked}
                />
              </div>
              <DefinitionList
                items={[
                  {
                    term: "Slug",
                    description: (
                      <span className="font-mono [overflow-wrap:anywhere]">{role.slug}</span>
                    ),
                  },
                  {
                    term: "Binds at",
                    description: (
                      <span>a {SCOPE_NOUN[role.scopeLevel]} or anything inside one</span>
                    ),
                  },
                  { term: "Kind", description: role.isSystem ? "built-in" : "custom" },
                ]}
              />
            </SettingsSection>
          ),
        },
      ]}
    />
  );
}
