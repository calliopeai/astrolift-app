import { gql } from "@apollo/client";

/** Every field of the person's UI preferences (#2154), as lib/ui-prefs-sync reads them. */
export const UI_PREFERENCES_FIELDS = gql`
  fragment UiPreferencesFields on AstroliftUiPreferences {
    homeLayout
    homeLayoutAsked
    fleetView
    workflowView
    appView
    flowParticles
    motion
    restrictedSettings
    restrictedSettingsChoice
    restrictedSettingsOrgDefault
    appearance
  }
`;

/** The signed-in person's UI preferences. Self-service; a read creates no row. */
export const MY_UI_PREFERENCES = gql`
  query MyUiPreferences {
    astroliftMyUiPreferences {
      ...UiPreferencesFields
    }
  }
  ${UI_PREFERENCES_FIELDS}
`;
