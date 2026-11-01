# GitHub App installation selection

GitHub installation and Astrolift registration are separate steps. Under **Providers → Source**, verify the active App connection's name and GitHub account. An installation on GitHub alone does not register that account in Astrolift, and a disabled pending connection is not used by CI.

To register another account for an existing App, choose **Connect to GitHub → Connect an existing GitHub App**. Enter that App's numeric ID and existing private key, and specify the **GitHub org / user login** that owns the repository. Astrolift verifies the App credentials and selected installation before saving. The same App may have a separate connection for each account in one Astrolift organization. Re-adopting or rotating one account preserves the others.

For a repository such as `owner/repository`, workflow writes, webhook setup and CI secrets prefer the active installation recorded on `owner`. Confirm both the App identity and account: two Apps with similar names may have different installation inventories and credentials. If only a different account is registered, connect the intended account before running `astro ci setup <app-slug>`.

Older connection records may carry stale account labels. Re-adopting the intended App with the explicit repository owner refreshes its installation metadata. This fixes registration; it does not grant the App additional GitHub permissions or repository access.
