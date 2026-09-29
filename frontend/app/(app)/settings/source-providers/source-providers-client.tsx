"use client";

import {
  DeployKeysView,
  SourceHostsView,
  SourceProvidersScreen,
} from "@/components/screens/settings/source-providers/SourceProvidersScreen";
import {
  useDeployKeys,
  useSourceHosts,
  useSourceSection,
} from "@/components/screens/settings/source-providers/use-source-providers";

import { AddClientIdDialog } from "./add-client-id-dialog";
import { ConnectGitHubDialog } from "./connect-github-dialog";
import { ConnectGitLabDialog } from "./connect-gitlab-dialog";
import { ConnectSourceDialog } from "./connect-source-dialog";
import { GenerateSshKeyDialog } from "./generate-ssh-key-dialog";

/**
 * The SCM panel rendered under /providers' Source tab. The screen owns the
 * markup; each section and each sheet is a container here, so its hook runs
 * only while it is mounted.
 */
export function SourceProvidersPanel() {
  return (
    <SourceProvidersScreen
      section={useSourceSection()}
      hosts={<HostsSection />}
      keys={<KeysSection />}
    />
  );
}

function HostsSection() {
  return (
    <SourceHostsView
      {...useSourceHosts()}
      renderConnectGithub={(open, onOpenChange) => (
        <ConnectGitHubDialog open={open} onOpenChange={onOpenChange} />
      )}
      renderConnectGitlab={(open, onOpenChange) => (
        <ConnectGitLabDialog open={open} onOpenChange={onOpenChange} />
      )}
      renderConnectSource={(open, onOpenChange) => (
        <ConnectSourceDialog open={open} onOpenChange={onOpenChange} />
      )}
      renderAddClientId={(connection, onClose) => (
        <AddClientIdDialog connection={connection} onClose={onClose} />
      )}
    />
  );
}

function KeysSection() {
  return (
    <DeployKeysView
      {...useDeployKeys()}
      renderGenerateKey={(open, onOpenChange) => (
        <GenerateSshKeyDialog open={open} onOpenChange={onOpenChange} />
      )}
    />
  );
}
