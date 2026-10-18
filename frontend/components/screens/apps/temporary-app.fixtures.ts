import type { TemporaryAppRules } from "./temporary-app-model";

export const TEMPORARY_RULES: TemporaryAppRules = {
  policy: {
    minTtlSeconds: 900,
    maxTtlSeconds: 7776000,
    temporaryOnlyForMembers: false,
    ttlRequiredSurfaces: [],
    dataGraceDays: 7,
  },
  permanentAllowed: true,
};
