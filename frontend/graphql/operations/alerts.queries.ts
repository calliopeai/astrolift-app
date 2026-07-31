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

/**
 * Cursor-paginated companions to ``LIST_ALERT_RULES`` / ``LIST_ALERT_EVENTS``
 * (#1230). ``{ items, nextCursor, totalCount }`` out, ``limit`` + ``after``
 * in, so ``useCursorTable`` walks the whole result set on the server.
 *
 * ``target`` / ``targetId`` / ``activeOnly`` (rules) and ``ruleId`` /
 * ``unresolvedOnly`` (events) are static controller variables — changing one
 * changes the question and restarts the walk at page one. Neither field takes
 * a sort argument, so a table over them declares no ``sortVariable`` and no
 * ``Column.sortKey``.
 *
 * ``$limit: Int`` is nullable against the schema's ``limit: Int! = 50``, and
 * ``$activeOnly`` / ``$unresolvedOnly`` are nullable against non-null
 * arguments for the same reason: those arguments carry defaults, which is
 * what makes a nullable variable legal in that position.
 *
 * These documents interpolate the file's field-list constants rather than
 * spreading named fragments because every other document here does, and the
 * file is excluded from the codegen document set for exactly that reason
 * (see ``codegen.ts``). Converting all of them together — and dropping the
 * exclusion — is a separate change; splitting the file's style in half
 * without being able to re-run codegen would be worse than either end state.
 */
export const LIST_ALERT_RULES_PAGE = gql`
  query ListAlertRulesPage(
    $target: String
    $targetId: String
    $activeOnly: Boolean
    $search: String
    $limit: Int
    $after: String
  ) {
    astroliftAlertRulesPage(
      target: $target
      targetId: $targetId
      activeOnly: $activeOnly
      search: $search
      limit: $limit
      after: $after
    ) {
      items {
        ${ALERT_RULE_FIELDS}
      }
      nextCursor
      totalCount
    }
  }
`;

export const LIST_ALERT_EVENTS_PAGE = gql`
  query ListAlertEventsPage(
    $ruleId: GUID
    $unresolvedOnly: Boolean
    $search: String
    $limit: Int
    $after: String
  ) {
    astroliftAlertEventsPage(
      ruleId: $ruleId
      unresolvedOnly: $unresolvedOnly
      search: $search
      limit: $limit
      after: $after
    ) {
      items {
        ${ALERT_EVENT_FIELDS}
      }
      nextCursor
      totalCount
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
      errors {
        code
        message
      }
      data {
        id
        deleted
      }
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
