/**
 * Services types — facade over the codegen output (#401).
 *
 * The schema carries every field; this file narrows the shapes our
 * UI consumes so components don't import the giant generated module
 * directly.
 */

import type {
  AstroliftManagedService as GeneratedManagedService,
  AstroliftManagedServiceConnection as GeneratedManagedServiceConnection,
  AstroliftManagedServiceConnectionKey as GeneratedManagedServiceConnectionKey,
  AstroliftManagedServiceObject as GeneratedManagedServiceObject,
  AstroliftManagedServiceObjects as GeneratedManagedServiceObjects,
  AstroliftManagedServiceQueueDepth as GeneratedManagedServiceQueueDepth,
  AstroliftManagedServiceTestEmailResult as GeneratedManagedServiceTestEmailResult,
} from "@/graphql/__generated__/schema";

export type AstroliftManagedService = Pick<
  GeneratedManagedService,
  | "id"
  | "name"
  | "kind"
  | "variant"
  | "status"
  | "statusError"
  | "environmentName"
  | "registeredAppSlug"
  | "createdAt"
  | "updatedAt"
  | "lastActionAt"
  | "lastActionKind"
> & {
  /** JSON scalar — opaque shape; callers cast as needed. */
  config: Record<string, unknown>;
};

export type AstroliftManagedServiceConnectionKey =
  GeneratedManagedServiceConnectionKey;

export type AstroliftManagedServiceConnection =
  GeneratedManagedServiceConnection;

export type AstroliftManagedServiceObject = GeneratedManagedServiceObject;

export type AstroliftManagedServiceObjects = GeneratedManagedServiceObjects;

export type AstroliftManagedServiceQueueDepth =
  GeneratedManagedServiceQueueDepth;

export type AstroliftManagedServiceTestEmailResult =
  GeneratedManagedServiceTestEmailResult;
