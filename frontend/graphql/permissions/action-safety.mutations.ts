import { gql } from "@apollo/client";

/** An organization's rule for one gated action (#2202, #2203). */
export const SET_ACTION_SAFETY_RULE = gql`
  mutation SetActionSafetyRule($input: SetActionSafetyRuleInput!) {
    setActionSafetyRule(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        action
        scopeKind
        enabled
        allowSelfApproval
      }
    }
  }
`;
