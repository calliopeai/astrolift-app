import { describe, expect, it } from "vitest";

import type { AstroliftSourceConnection } from "@/graphql/scm/scm.types";

import { connection } from "./settings-source-providers.fixtures";
import { narrowHosts, offersInstallOnGithub, SOURCE_HOSTS_LIST } from "./source-providers-list";

/**
 * The Hosts Status filter narrows on the status the Status column shows,
 * the server's check (#2601), not the stored isActive switch: an App with no
 * installation on its account is switched on and is not active.
 */
const rows = [
  connection("installed", { accountLogin: "calliopeai" }),
  connection("missing", {
    accountLogin: "ConflictHQ",
    installationId: "",
    status: "INSTALLATION_MISSING",
  }),
  connection("refused", { accountLogin: "acme-legacy", status: "INACTIVE" }),
  connection("unchecked", { accountLogin: "acme-labs", status: "UNVERIFIED" }),
];

const names = (status: string) => narrowHosts(rows, { status }).rows.map((c) => c.name);

describe("Hosts status filter", () => {
  it("offers every status the server reports", () => {
    const status = SOURCE_HOSTS_LIST.fields.find((f) => f.key === "status");
    expect(status?.options?.map((o) => o.value)).toEqual([
      "active",
      "inactive",
      "installation_missing",
      "unverified",
    ]);
  });

  it("narrows on the server's status, not the stored switch", () => {
    expect(names("active")).toEqual(["installed"]);
    expect(names("installation_missing")).toEqual(["missing"]);
    expect(names("inactive")).toEqual(["refused"]);
    expect(names("unverified")).toEqual(["unchecked"]);
  });

  it("reads the stored switch for a row whose query asked for no status", () => {
    const bare = connection("bare", {
      isActive: false,
      status: undefined,
      statusDetail: undefined,
    });
    expect(narrowHosts([bare], { status: "inactive" }).rows).toEqual([bare]);
  });
});

/**
 * Install on GitHub (#2601) runs the App's install flow for a row, and the
 * backend stores the installation back on that row only for a registered App
 * on github.com with an account (`connection_status.can_relink`).
 */
describe("Install on GitHub", () => {
  const missing: Partial<AstroliftSourceConnection> = {
    accountLogin: "ConflictHQ",
    installationId: "",
    status: "INSTALLATION_MISSING",
  };

  it("is offered on a registered github.com App whose installation is missing", () => {
    for (const apiBaseUrl of ["https://api.github.com", "https://api.github.com/", ""]) {
      expect(offersInstallOnGithub(connection("missing", { ...missing, apiBaseUrl }))).toBe(true);
    }
  });

  it("is not offered where the install flow cannot put an installation on the row", () => {
    const others: Partial<AstroliftSourceConnection>[] = [
      { status: "ACTIVE" },
      { status: "INACTIVE" },
      { status: "UNVERIFIED" },
      { kind: "github_pat" },
      { oauthClientId: "" },
      { accountLogin: "" },
      { apiBaseUrl: "https://ghe.example.test/api/v3" },
    ];
    for (const patch of others) {
      expect(offersInstallOnGithub(connection("other", { ...missing, ...patch }))).toBe(false);
    }
  });
});
