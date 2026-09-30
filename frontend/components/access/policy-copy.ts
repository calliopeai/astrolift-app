import { conditionError, type PolicyCondition } from "./policy-model";

const ERRORS = {
  "Pick at least one day.": "error.days",
  "Add at least one range, like 09:00-18:00.": "error.ranges",
  "Hours are HH:MM-HH:MM, like 09:00-18:00.": "error.hours",
  "Name a time zone, like America/Los_Angeles.": "error.zone",
  "Add at least one CIDR, like 10.0.0.0/8.": "error.cidrs",
  "Each entry is a CIDR, like 10.0.0.0/8.": "error.cidrFormat",
  "At least one approver.": "error.approver",
  "Name at least one environment.": "error.environment",
  "Pick at least one factor.": "error.factor",
  "At least one minute.": "error.minute",
} as const;

type ConditionErrorKey = (typeof ERRORS)[keyof typeof ERRORS];
export function localizedConditionError(
  condition: PolicyCondition,
  t: (key: ConditionErrorKey) => string
): string | null {
  const error = conditionError(condition);
  if (!error) return null;
  const key = Object.prototype.hasOwnProperty.call(ERRORS, error)
    ? ERRORS[error as keyof typeof ERRORS]
    : null;
  return key ? t(key) : error;
}
