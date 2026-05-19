import { gql } from "@apollo/client";

const ALERT_RULE_FIELDS = `
  id
  name
  target
  targetId
  managedServiceId
  severity
  predicate
  notifyChannels
  isActive
  organizationSlug
  createdAt
  updatedAt
  activeMute {
    id
    ttlUntil
    reason
    createdBy
  }
`;

const ALERT_EVENT_FIELDS = `
  id
  ruleId
  severity
  firedAt
  resolvedAt
  acknowledgedAt
  summary
  detail
`;

export const LIST_ALERT_RULES = gql`
  query ListAlertRules(
    $target: String
    $targetId: String
    $activeOnly: Boolean = true
  ) {
    astroliftAlertRules(
      target: $target
      targetId: $targetId
      activeOnly: $activeOnly
    ) {
      ${ALERT_RULE_FIELDS}
    }
  }
`;

export const LIST_ALERT_EVENTS = gql`
  query ListAlertEvents(
    $ruleId: GUID
    $unresolvedOnly: Boolean = false
    $limit: Int = 100
  ) {
    astroliftAlertEvents(
      ruleId: $ruleId
      unresolvedOnly: $unresolvedOnly
      limit: $limit
    ) {
      ${ALERT_EVENT_FIELDS}
    }
  }
`;

export const CREATE_ALERT_RULE = gql`
  mutation CreateAlertRule($input: CreateAlertRuleInput!) {
    createAlertRule(input: $input) {
      ok
      errors { code message field }
      data { ${ALERT_RULE_FIELDS} }
    }
  }
`;

export const UPDATE_ALERT_RULE = gql`
  mutation UpdateAlertRule($input: UpdateAlertRuleInput!) {
    updateAlertRule(input: $input) {
      ok
      errors { code message field }
      data { ${ALERT_RULE_FIELDS} }
    }
  }
`;

export const DELETE_ALERT_RULE = gql`
  mutation DeleteAlertRule($input: DeleteAlertRuleInput!) {
    deleteAlertRule(input: $input) {
      ok
      errors { code message }
      data { id deleted }
    }
  }
`;

export const ACKNOWLEDGE_ALERT_EVENT = gql`
  mutation AcknowledgeAlertEvent($input: AcknowledgeAlertEventInput!) {
    acknowledgeAlertEvent(input: $input) {
      ok
      errors { code message }
      data { ${ALERT_EVENT_FIELDS} }
    }
  }
`;

export const MUTE_ALERT_RULE = gql`
  mutation MuteAlertRule($input: MuteAlertRuleInput!) {
    muteAlertRule(input: $input) {
      ok
      errors { code message field }
      data { ${ALERT_RULE_FIELDS} }
    }
  }
`;

export const UNMUTE_ALERT_RULE = gql`
  mutation UnmuteAlertRule($input: UnmuteAlertRuleInput!) {
    unmuteAlertRule(input: $input) {
      ok
      errors { code message field }
      data { ${ALERT_RULE_FIELDS} }
    }
  }
`;
