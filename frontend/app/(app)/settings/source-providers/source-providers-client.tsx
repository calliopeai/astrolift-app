"use client";

import { SourceProvidersScreen } from "@/components/screens/settings/source-providers/SourceProvidersScreen";
import { useSourceProviders } from "@/components/screens/settings/source-providers/use-source-providers";

import { AddClientIdDialog } from "./add-client-id-dialog";
import { ConnectGitHubDialog } from "./connect-github-dialog";
import { ConnectGitLabDialog } from "./connect-gitlab-dialog";
import { ConnectSourceDialog } from "./connect-source-dialog";
import { GenerateSshKeyDialog } from "./generate-ssh-key-dialog";

/**
 * The SCM panel rendered under /providers' Source tab. The screen owns the
 * markup; the sheets are containers here so each one's hook runs only
 * while it is mounted.
 */
export function SourceProvidersPanel() {
  return (
    <SourceProvidersScreen
      {...useSourceProviders()}
      renderConnectGithub={(open, onOpenChange) => (
        <ConnectGitHubDialog open={open} onOpenChange={onOpenChange} />
      )}
      renderConnectGitlab={(open, onOpenChange) => (
        <ConnectGitLabDialog open={open} onOpenChange={onOpenChange} />
      )}
      renderConnectSource={(open, onOpenChange) => (
        <ConnectSourceDialog open={open} onOpenChange={onOpenChange} />
      )}
      renderGenerateKey={(open, onOpenChange) => (
        <GenerateSshKeyDialog open={open} onOpenChange={onOpenChange} />
      )}
      renderAddClientId={(connection, onClose) => (
        <AddClientIdDialog connection={connection} onClose={onClose} />
      )}
    />
  );
}
