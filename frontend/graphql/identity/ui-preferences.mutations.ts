import { gql } from "@apollo/client";

import { UI_PREFERENCES_FIELDS } from "./ui-preferences.queries";

/** Save some UI preferences: an omitted field is unchanged, an explicit null resets it. */
export const UPDATE_MY_UI_PREFERENCES = gql`
  mutation UpdateMyUiPreferences($input: UpdateMyUiPreferencesInput!) {
    updateMyUiPreferences(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        ...UiPreferencesFields
      }
    }
  }
  ${UI_PREFERENCES_FIELDS}
`;
