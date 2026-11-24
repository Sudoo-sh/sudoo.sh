// The demo's build options (src/sudoo/projects/build_options.py): what a new commit does to a branch, how a new
// development database is made and which tests its builds run. A branch's value wins over its project's, and the
// project's over the panel's setting; production always updates and runs no tests.

import type { BranchBuildOptions, BuildOptionSource, BuildOptionValues, Stage } from "@/api/types";
import { options, projects, stageOf, type DemoProject } from "./projects";
import { iso, Problem } from "./util";

export const KEYS: (keyof BuildOptionValues)[] = [
  "on_new_commit",
  "module_install",
  "modules",
  "test_suite",
  "test_tags",
  "demo_data",
];
const CHOICES: Partial<Record<keyof BuildOptionValues, string[]>> = {
  on_new_commit: ["new_build", "update", "nothing"],
  module_install: ["repository", "all", "list"],
  test_suite: ["off", "selection", "custom"],
};
const MODULE = /^[a-z0-9_]{1,100}$/;
const TAG = /^-?[A-Za-z0-9_/.:*\[\]]{1,200}$/;

/** Each branch's own options and who changed them last, per project. */
const own = new Map<string, Record<string, Partial<BuildOptionValues>>>();
const changed = new Map<string, Record<string, { by: string; at: number }>>();

export function seedBuildOptions() {
  own.clear();
  changed.clear();
  // a branch that only records its commits, and one with tests of its own
  own.set("acme-retail", {
    "loyalty-points": { on_new_commit: "nothing" },
    "ecommerce-bundles": { test_suite: "custom", test_tags: "/acme_bundles,-slow" },
  });
  changed.set("acme-retail", {
    "loyalty-points": { by: "ines", at: Date.now() - 3 * 86_400_000 },
    "ecommerce-bundles": { by: "karim", at: Date.now() - 86_400_000 },
  });
}

export const ownOf = (project: DemoProject, branch: string): Partial<BuildOptionValues> =>
  own.get(project.id)?.[branch] ?? {};

export function projectDefault(
  project: DemoProject,
  key: keyof BuildOptionValues,
  stage: Stage,
): BuildOptionValues[typeof key] {
  const o = options(project);
  if (key === "on_new_commit" && stage === "staging") return o.staging_on_push === "rebuild" ? "new_build" : "update";
  if (key === "test_suite" && stage === "staging" && !o.staging_tests) return "off";
  const map = {
    on_new_commit: o.development_on_new_commit,
    module_install: o.module_install,
    modules: o.modules,
    test_suite: o.test_suite,
    test_tags: o.test_tags,
    demo_data: o.development_demo_data,
  };
  return map[key];
}

/** What applies to a branch now, and where each value comes from. */
export function effective(project: DemoProject, branch: string) {
  const stage = stageOf(project, branch);
  const mine = ownOf(project, branch);
  const values = {} as Record<keyof BuildOptionValues, unknown>;
  const sources = {} as Record<keyof BuildOptionValues, BuildOptionSource>;
  for (const key of KEYS) {
    if (mine[key] !== undefined) [values[key], sources[key]] = [mine[key], "branch"];
    else [values[key], sources[key]] = [projectDefault(project, key, stage), "project"];
  }
  if (stage === "production") {
    values.on_new_commit = "update";
    values.test_suite = "off";
    sources.on_new_commit = sources.test_suite = "production";
  }
  if (values.module_install === "all" && values.test_suite !== "off")
    [values.test_suite, sources.test_suite] = ["off", "module_install"];
  if (values.module_install === "list" && !values.modules)
    [values.module_install, sources.module_install] = ["repository", "empty_list"];
  if (values.test_suite === "custom" && !values.test_tags)
    [values.test_suite, sources.test_suite] = ["off", "empty_tags"];
  return { stage, values: values as unknown as BuildOptionValues, sources };
}

export function branchOptions(project: DemoProject, branch: string): BranchBuildOptions {
  const { stage, values, sources } = effective(project, branch);
  const last = changed.get(project.id)?.[branch];
  const o = options(project);
  return {
    project: project.id,
    branch,
    stage,
    options: values,
    sources,
    own: ownOf(project, branch),
    project_defaults: Object.fromEntries(
      KEYS.map((k) => [k, projectDefault(project, k, stage)]),
    ) as unknown as BuildOptionValues,
    changed_by: last?.by ?? null,
    changed_at: last ? iso(last.at) : null,
    commit_statuses: {
      enabled: Boolean(o.commit_statuses),
      available: statusesAvailable(project),
      context: `sudoo/${stage}`,
    },
    links: { self: `/api/v1/projects/${project.id}/branches/${branch}/options` },
  };
}

/** The GitHub App may write commit statuses everywhere but in one project, to show the other case. */
export const statusesAvailable = (project: DemoProject) => project.id !== "nile-logistics";

/** A value checked as the real panel does (build_options.clean); null follows the project again. */
export function clean(key: string, value: unknown): unknown {
  if (value === null) return null;
  const choices =
    CHOICES[key as keyof BuildOptionValues] ??
    (key === "development_on_new_commit" ? CHOICES.on_new_commit : undefined);
  if (choices) {
    if (!choices.includes(String(value))) throw new Problem(400, `${key}: ${choices.join(" or ")}.`);
    return value;
  }
  if (key === "modules") {
    const names = (Array.isArray(value) ? value.map(String) : String(value).split(","))
      .map((m) => m.trim())
      .filter(Boolean);
    const wrong = names.find((m) => !MODULE.test(m));
    if (wrong)
      throw new Problem(
        400,
        `modules: ${wrong.slice(0, 40)} is not a module name Odoo accepts (lower-case letters, digits and _).`,
      );
    return [...new Set(names)].join(",");
  }
  if (key === "test_tags") {
    const tags = String(value)
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean);
    const wrong = tags.find((t) => !TAG.test(t));
    if (wrong)
      throw new Problem(
        400,
        `test_tags: ${wrong.slice(0, 40)} is not a test tag Odoo accepts, such as /sale_custom or -slow.`,
      );
    return tags.join(",");
  }
  if (["demo_data", "staging_tests", "commit_statuses"].includes(key)) {
    if (typeof value !== "boolean") throw new Problem(400, `${key}: true or false.`);
    return value;
  }
  throw new Problem(400, `Unknown option ${key.slice(0, 40)}. Options: ${KEYS.join(", ")}.`);
}

/** Why a combination cannot be kept (build_options.problem). */
export function problem(values: Partial<BuildOptionValues>): string | null {
  if (values.module_install === "list" && !values.modules)
    return 'module_install "list" needs modules: the module names to install, comma-separated.';
  if (values.test_suite === "custom" && !values.test_tags)
    return 'test_suite "custom" needs test_tags (Odoo\'s --test-tags syntax, such as /sale_custom,-slow).';
  return null;
}

/** PUT .../options: set or reset the branch's own values. */
export function changeBranchOptions(
  project: DemoProject,
  branch: string,
  body: Record<string, unknown>,
  user: string,
): BranchBuildOptions {
  const unknown = Object.keys(body).filter((k) => !(KEYS as string[]).includes(k));
  if (unknown.length || !Object.keys(body).length) throw new Problem(400, `Send one or more of: ${KEYS.join(", ")}.`);
  const values = Object.fromEntries(Object.entries(body).map(([k, v]) => [k, clean(k, v)]));
  const mine: Record<string, unknown> = { ...ownOf(project, branch) };
  for (const [k, v] of Object.entries(values))
    if (v === null) delete mine[k];
    else mine[k] = v;
  const stage = stageOf(project, branch);
  const merged = Object.fromEntries(
    KEYS.map((k) => [k, mine[k] ?? projectDefault(project, k, stage)]),
  ) as Partial<BuildOptionValues>;
  const refused = problem(merged);
  if (refused) throw new Problem(400, refused);
  own.set(project.id, { ...(own.get(project.id) ?? {}), [branch]: mine as Partial<BuildOptionValues> });
  changed.set(project.id, { ...(changed.get(project.id) ?? {}), [branch]: { by: user, at: Date.now() } });
  return branchOptions(project, branch);
}

export const projectById = (id: string) => projects.get(id);
