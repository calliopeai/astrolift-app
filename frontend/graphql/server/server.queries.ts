import { gql } from "@apollo/client";

// Public, unauthenticated install handshake (#479). We only pull the
// feature-flag allow-list here — the UI gates surfaces on these keys
// (e.g. `zentinelle.enabled` gates the Zentinelle-badged agent tabs).
export const SERVER_INFO = gql`
  query AstroliftServerInfo {
    astroliftServerInfo {
      featureFlags {
        key
        enabled
      }
    }
  }
`;
