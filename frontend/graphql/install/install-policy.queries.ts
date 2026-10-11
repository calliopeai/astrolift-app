import { gql } from "@apollo/client";

/** What the install withholds from Astrolift, with the reason (calliope-installer#447). */
export const WITHHELD_CAPABILITIES = gql`
  query WithheldCapabilities {
    astroliftWithheldCapabilities {
      capability
      reason
    }
  }
`;

/** The model the install serves on its own GPUs, or null (calliope-installer#446). */
export const INSTALL_MANAGED_MODEL = gql`
  query InstallManagedModel {
    astroliftInstallManagedModel {
      modelId
      replicas
    }
  }
`;
