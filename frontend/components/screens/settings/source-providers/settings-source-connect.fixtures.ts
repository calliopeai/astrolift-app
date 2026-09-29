import type { ConnectGitHubDialogViewProps } from "./ConnectGitHubDialog";
import type { ConnectGitLabDialogViewProps } from "./ConnectGitLabDialog";
import type { ConnectSourceDialogViewProps } from "./ConnectSourceDialog";

/** Hand-typed props for the three connect sheets (group settings-source-connect). */

const noop = () => {};
const accepted = async () => true;

const sheet = { open: true, onOpenChange: noop };

export const CONNECT_SOURCE_PROPS: ConnectSourceDialogViewProps = {
  ...sheet,
  loading: false,
  onConnect: accepted,
};

export const CONNECT_GITHUB_PROPS: ConnectGitHubDialogViewProps = {
  ...sheet,
  loading: false,
  startManifestFlow: noop,
  onConnectExisting: accepted,
};

export const CONNECT_GITLAB_PROPS: ConnectGitLabDialogViewProps = {
  ...sheet,
  loading: false,
  callbackUrl: "https://astrolift.acme.example/app/auth1/scm/gitlab/callback",
  copyCallback: accepted,
  onConnect: accepted,
};

export const LONG_CALLBACK_URL =
  "https://astrolift-control-plane.eu-central-1.internal.acme-holdings-group.example/app/auth1/scm/gitlab/callback";

export const LONG_TEXT =
  "acme-holdings-group-platform-engineering-production-source-connection-with-a-very-long-name";
