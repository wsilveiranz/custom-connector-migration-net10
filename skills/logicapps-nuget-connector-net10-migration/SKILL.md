---
name: logicapps-nuget-connector-net10-migration
description: Migrate NuGet-based Azure Logic Apps Standard projects that host custom built-in connectors (service providers) from .NET 8 to .NET 10. Use for project/package configuration, host.json and extension bundle cleanup, local worker-runtime settings, VS Code paths, and reporting the Azure app-setting changes. Not for custom-code (local function) workspaces, bundle-based Logic Apps without the Workflows NuGet package, Consumption workflows, ordinary .NET apps, or cloud deployment.
license: MIT
---
# Logic Apps NuGet connector .NET 10 migration

Read [the migration procedure](references/migration.md) before editing. Resolve bundled
files relative to this skill, not the target repository. Python 3.11+ is required only
for the helper; do not install dependencies or start services merely by activating this skill.

## Recognize the layout first

This skill applies when a Logic App host `.csproj` sits next to `host.json` and directly
references `Microsoft.Azure.Workflows.WebJobs.Extension`, usually with a `ProjectReference`
to a connector library that registers a service provider. The whole host moves to .NET 10.

If the project has `LogicAppFolderToPublish` (custom-code local functions), stop and use
`logicapps-custom-code-net10-migration`. That migration keeps the host on .NET 8 and
requires the extension bundle; the rules here are the opposite. Never mix the two.

## Procedure

1. Establish the solution root and the Logic App host `.csproj`. Read repository
   instructions, dirty changes, `global.json`, `Directory.Build.*`, central package
   management, workspace and task files. Do not inspect or change C# or workflow
   definitions; this migration is configuration-only.
2. Run the bundled helper in its default dry-run mode:

   ```powershell
   python "<skill root>\scripts\migrate.py" --root "<solution root>"
   ```

   Add `--project "<host csproj>"` when several hosts exist. Add
   `--connector-version 2.0.0` (or another numeric version) to set `<Version>` on
   connector projects so the loaded assembly is identifiable. Read all blockers,
   warnings, and manual follow-ups. The helper never prints settings values.
3. For a requested migration, rerun with the same options and `--apply`. Do not apply
   for a comparison or plan-only request. The helper checks for concurrent edits and
   rolls back its own writes on failure; it is not a persistent backup.
4. If the helper blocks (central package management, conditions, imports, multi-targeting,
   encrypted settings, custom-code layout), do not bypass it. Read the owning configuration,
   agree scope with the user, and make targeted edits that achieve the same end state.
   Update scripts the helper lists as manual follow-up.
5. Validate with the repository build and tests, using the approved NuGet source. Pass
   `-p:EnableProjectDataOnBuild=false` if the repository already uses it. Do not suppress
   NU1605 or vulnerability warnings to get a clean build.
6. Local runtime check, only with permission for trigger and connector effects: make sure
   Core Tools 4.16.0 or later (main host on .NET 10) is the `func` on PATH. The Logic Apps
   VS Code extension installs one under `%USERPROFILE%\.azurelogicapps\dependencies\FuncCoreTools`.
   Confirm the connector appears in the built-in catalog and a workflow using it runs.
   Follow the Logic Apps runtime skill when available. Stop only task-owned processes.
7. Report configuration migrated, build verified, and local execution verified as
   separate states. Always report the Azure prerequisites (below); do not perform them.

## Azure prerequisites (report only)

- Delete app settings `AzureFunctionsJobHost__extensionBundle__id` and
  `AzureFunctionsJobHost__extensionBundle__version`. They override `host.json`.
  Apps created in the portal usually have them.
- Delete `LOGICAPP_INPROC_REDIRECT` if present; it forces the legacy .NET 8 host.
- Keep `FUNCTIONS_WORKER_RUNTIME=dotnet`, `FUNCTIONS_EXTENSION_VERSION=~4`, and
  `APP_KIND=workflowApp`. `FUNCTIONS_INPROC_NET8_ENABLED` is not required.
  `netFrameworkVersion` stays `v8.0`; it does not decide the loaded CLR.
- Deploy `bin\Release\net10.0\publish`. For an existing app: stop, change settings,
  deploy, start. Verify no `'/serviceProviders/<id>' ... is not valid` error, a callback
  URL on the affected workflows, and a successful run.

## Invariants

- No C#, workflow, connection, or authentication changes. No `Program.cs` or worker project.
- `dotnet-isolated` is the local-only value in `local.settings.json`. Azure and
  `cloud.settings.json` keep `dotnet`. Never use `node`.
- Never add `extensionBundle` to `host.json` of a NuGet-based host.
- Preserve newer package versions; warn instead of downgrading.
- Do not change Azure resources, deploy, install SDKs or Core Tools, or restore packages
  inside the helper. Do not print or commit settings values.

Installation and test commands are in [README.md](README.md).
