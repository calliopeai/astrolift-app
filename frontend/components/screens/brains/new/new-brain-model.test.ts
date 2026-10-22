import { describe, expect, it } from "vitest";

import { CELLS } from "../brain-cells.fixtures";
import { CONNECTIONS, DRAFT, PROJECTS } from "./new-brain.fixtures";
import {
  createInput,
  creationSteps,
  draftFieldOf,
  draftProblems,
  EMPTY_DRAFT,
  failedStep,
  firstStepWithProblem,
  slugFromName,
  stepOfServerField,
  stepReady,
  teamsOf,
  visibilitiesFor,
  withDefaults,
  withName,
} from "./new-brain-model";

const context = { connections: CONNECTIONS, projects: PROJECTS };

describe("the create request's checks", () => {
  it("passes a filled-in draft", () => {
    expect(draftProblems(DRAFT, context)).toEqual({});
    expect(firstStepWithProblem(draftProblems(DRAFT, context))).toBeNull();
  });

  it("holds each step until its own fields are filled in", () => {
    const problems = draftProblems(EMPTY_DRAFT, context);
    expect(problems).toMatchObject({
      connectionId: "required",
      repo: "required",
      slug: "required",
      teamId: "required",
      projectId: "required",
    });
    expect(stepReady(1, problems)).toBe(false);
    expect(stepReady(3, problems)).toBe(true);
    expect(firstStepWithProblem(problems)).toBe(1);
  });

  it("checks shapes the way the server does first", () => {
    const problems = draftProblems(
      {
        ...DRAFT,
        repo: "acme/../escape",
        templateRef: "v1 2",
        slug: "Platform_Brain",
        environmentName: "Prod Main",
      },
      context
    );
    expect(problems).toEqual({
      repo: "repoFormat",
      templateRef: "refFormat",
      slug: "slugFormat",
      environmentName: "envFormat",
    });
  });

  it("asks for a -brain slug when the edition has no deploy.app_slug (#2444)", () => {
    const noAppSlug = { ...context, supportsAppSlug: false };
    expect(draftProblems({ ...DRAFT, slug: "handbook" }, noAppSlug).slug).toBe("needsAppSlug");
    expect(draftProblems({ ...DRAFT, slug: "handbook-brain" }, noAppSlug).slug).toBeUndefined();
    // Unknown until the edition is read: the server still checks.
    expect(draftProblems({ ...DRAFT, slug: "handbook" }, context).slug).toBeUndefined();
  });

  it("refuses an unsupported cell once the matrix is in hand", () => {
    const cell = { ...DRAFT.substrate.cell, authority: "d1" as const };
    const draft = { ...DRAFT, substrate: { ...DRAFT.substrate, cell } };
    expect(draftProblems(draft, { ...context, matrix: CELLS }).substrate).toBe("required");
    expect(draftProblems(draft, context).substrate).toBeUndefined();
  });

  it("refuses a project of another team: a layer belongs to its project's team", () => {
    expect(draftProblems({ ...DRAFT, projectId: PROJECTS[2]!.id }, context).projectId).toBe(
      "required"
    );
  });

  it("offers internal visibility on GitHub and GitLab only", () => {
    expect(visibilitiesFor("github_pat")).toEqual(["PRIVATE", "INTERNAL", "PUBLIC"]);
    expect(visibilitiesFor("gitlab_oauth_user")).toContain("INTERNAL");
    expect(visibilitiesFor("gitea_pat")).toEqual(["PRIVATE", "PUBLIC"]);
    expect(
      draftProblems({ ...DRAFT, connectionId: CONNECTIONS[2]!.id, visibility: "INTERNAL" }, context)
        .visibility
    ).toBe("required");
  });
});

describe("names and slugs", () => {
  it("suggests a slug from the name until the slug is typed", () => {
    expect(slugFromName("Café Playbooks: 2026!")).toBe("cafe-playbooks-2026");
    expect(slugFromName("  --  ")).toBe("");
    // A slug starts with a letter, so leading digits leave the suggestion.
    expect(slugFromName("2026 Plan")).toBe("plan");
    expect(slugFromName("x".repeat(80))).toHaveLength(63);
    const named = withName(EMPTY_DRAFT, "Sales Playbooks");
    expect(named.slug).toBe("sales-playbooks");
    expect(withName({ ...named, slug: "mine", slugTouched: true }, "Other").slug).toBe("mine");
  });
});

describe("the defaults filled in until the person picks", () => {
  it("are the first connection, the first team by slug and that team's first project", () => {
    const filled = withDefaults(EMPTY_DRAFT, context);
    expect(filled.connectionId).toBe(CONNECTIONS[0]!.id);
    expect(filled.teamId).toBe(PROJECTS[0]!.team.id);
    expect(filled.projectId).toBe(PROJECTS[0]!.id);
    expect(teamsOf(PROJECTS).map((t) => t.slug)).toEqual(["platform", "sales"]);
  });

  it("keep a pick that is still offered, and move a project that left the team", () => {
    const sales = PROJECTS[2]!.team.id;
    expect(withDefaults({ ...EMPTY_DRAFT, teamId: sales }, context).projectId).toBe(
      PROJECTS[2]!.id
    );
    expect(
      withDefaults({ ...EMPTY_DRAFT, teamId: sales, projectId: PROJECTS[0]!.id }, context).projectId
    ).toBe(PROJECTS[2]!.id);
  });
});

describe("server refusals", () => {
  it("land on the field and step they name", () => {
    expect(draftFieldOf("sourceConnectionId")).toBe("connectionId");
    expect(draftFieldOf("persistence")).toBe("substrate");
    expect(draftFieldOf("authority")).toBe("substrate");
    expect(draftFieldOf("slug")).toBe("slug");
    expect(draftFieldOf("deploy")).toBeNull();
    expect(stepOfServerField("repo")).toBe(1);
    expect(stepOfServerField("teamId")).toBe(2);
    expect(stepOfServerField("replicas")).toBe(3);
    expect(stepOfServerField(null)).toBe(4);
  });
});

describe("the request", () => {
  it("is createBrain's input, deploying only when the viewer may", () => {
    const input = createInput(
      { ...DRAFT, templateRef: " v1.4.0 ", name: " " },
      { canDeploy: false }
    );
    expect(input).toMatchObject({
      projectId: DRAFT.projectId,
      slug: "platform-brain",
      sourceConnectionId: DRAFT.connectionId,
      repo: "acme-platform/platform-brain",
      name: null,
      teamId: DRAFT.teamId,
      visibility: "PRIVATE",
      templateRef: "v1.4.0",
      environmentName: "production",
      deploy: false,
    });
    expect(input.substrate).toMatchObject({ authority: "FILES", persistence: [], image: null });
    expect(createInput(DRAFT, { canDeploy: true }).deploy).toBe(true);
  });
});

describe("a creation's progress", () => {
  it("marks what is done and what runs now", () => {
    expect(creationSteps("PENDING", { deploy: true }).map((s) => s.state)).toEqual([
      "current",
      "todo",
      "todo",
      "todo",
    ]);
    expect(creationSteps("SCAFFOLDED", { deploy: true }).map((s) => s.state)).toEqual([
      "done",
      "done",
      "current",
      "todo",
    ]);
    // The deploy runs while the state is DEPLOYING and is done only at READY.
    expect(creationSteps("DEPLOYING", { deploy: true }).map((s) => s.state)).toEqual([
      "done",
      "done",
      "done",
      "current",
    ]);
    expect(creationSteps("READY", { deploy: true }).every((s) => s.state === "done")).toBe(true);
  });

  it("skips the deploy step for a layer created without one", () => {
    expect(creationSteps("REGISTERED", { deploy: false }).map((s) => s.step)).toEqual([
      "REPO_READY",
      "SCAFFOLDED",
      "REGISTERED",
    ]);
  });

  it("marks where a failed creation stopped, from what it produced or was last seen at", () => {
    expect(failedStep({ commitSha: null, app: null, deploymentId: null }, null)).toBe("REPO_READY");
    expect(failedStep({ commitSha: "abc", app: null, deploymentId: null }, null)).toBe(
      "REGISTERED"
    );
    expect(failedStep({ commitSha: "abc", app: {}, deploymentId: "d" }, null)).toBe("DEPLOYING");
    expect(failedStep({ commitSha: null, app: null, deploymentId: null }, "REPO_READY")).toBe(
      "SCAFFOLDED"
    );
    expect(
      creationSteps("FAILED", { deploy: true, failed: "SCAFFOLDED" }).map((s) => s.state)
    ).toEqual(["done", "failed", "todo", "todo"]);
  });
});
