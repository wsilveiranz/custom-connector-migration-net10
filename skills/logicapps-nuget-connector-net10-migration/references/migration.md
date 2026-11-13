# Required migration procedure

## Why this migration differs from custom code

In a NuGet-based Logic App, the host project references the Workflows runtime package
and loads connector assemblies in the Functions host process. A `net10.0` connector needs
a host running on .NET 10. In a custom-code workspace, only the local-function library
moves to .NET 10 and the host stays on .NET 8. The two procedures have opposite rules
for the extension bundle and worker runtime, so keep them in separate skills.

## Required configuration

| File | Change |
|---|---|
| `global.json` (root and host folder) | `sdk.version` at least `10.0.403`; preserve newer |
| Host `.csproj` | `TargetFramework` `net10.0`; `Microsoft.NET.Sdk.Functions` at least 4.5.0 (4.6.0 also verified); `Microsoft.Azure.Workflows.WebJobs.Extension` 1.194.31 |
| Connector `.csproj` | `net10.0`; Workflows 1.194.31; add `Microsoft.Extensions.DependencyInjection.Abstractions` 10.0.0 and `Microsoft.Extensions.Primitives` 9.0.9 |
| Connector `<Version>` | Optional bump (for example 2.0.0) to identify the loaded assembly |
| Test projects that reference the connector | `net10.0` |
| `host.json` | No `extensionBundle` |
| `.vscode` settings and tasks | `bin\...\net8.0` paths to `net10.0` |
| `local.settings.json` | `FUNCTIONS_WORKER_RUNTIME=dotnet-isolated`; no `AzureFunctionsJobHost__extensionBundle__*` |
| `cloud.settings.json` (if present) | `FUNCTIONS_WORKER_RUNTIME=dotnet`; no bundle keys or `LOGICAPP_INPROC_REDIRECT` |

Versions are the reviewed baseline, not a universal minimum. The helper preserves newer
versions and warns about them.

### Dependency pins

Workflows 1.194.31 on `net10.0` produces NU1605 package downgrades for
`DependencyInjection.Abstractions` and `Primitives` in the connector graph. The two
direct references resolve them. Do not suppress NU1605. If restore shows other
downgrades, align only the affected packages.

### Extension bundle

A bundle replaces the Workflows extension that the host project ships. The host then
cannot see the connector, and every workflow using it fails validation with
`'/serviceProviders/<id>' ... is not valid`, so no callback URL is generated. Remove the
bundle from `host.json` and from `AzureFunctionsJobHost__extensionBundle__*` settings,
locally and in Azure. App settings override `host.json`.

### Worker runtime

- **Local:** Core Tools routes `dotnet` to its `in-proc8` child host, which runs .NET 8
  and fails with `Could not load ... Microsoft.Extensions.DependencyInjection, Version=9.0.0.0`
  or similar. `dotnet-isolated` selects the main host, which must target .NET 10
  (Core Tools 4.16.0 or later). npm Core Tools 4.12.0 runs on .NET 8 and fails with
  `System.Text.Json 9.0.0.0`. The Workflows extension and connector still load in-process;
  "isolated" only changes which host Core Tools selects.
- **Azure:** keep `dotnet`. The platform runs the .NET 10 out-of-process host when
  `LOGICAPP_INPROC_REDIRECT` is absent.

## Helper coverage

The helper edits direct, unconditional SDK-style projects and UTF-8 JSON or JSONC files,
preserving comments, BOMs, and line endings. It discovers the host (a `.csproj` next to
`host.json` that references the Workflows package), its connector project references,
and test projects that reference those connectors.

It blocks on `Directory.Build.*`, `Directory.Packages.props`, explicit imports,
conditional groups, multi-targeting, non-attribute package versions, floating versions,
encrypted local settings, comments inside a member it must remove, and custom-code
layouts. Scripts, pipelines, and workspace files that mention `net8.0` are reported for
manual review, not edited.

## Verification gates

1. Dry run, review, apply, and rerun the dry run: the second run must report no changes.
2. Restore and build with the approved source and the SDK selected by `global.json`.
   Run the unit tests.
3. With permission, start the host with Core Tools 4.16.0 or later and
   `dotnet-isolated`. Confirm no service provider validation error, the connector in the
   built-in catalog, and a successful workflow run.
4. Report Azure prerequisites separately. Configuration, build, local execution, and
   Azure execution are distinct evidence.

## Known issues not caused by the migration

- NuGet vulnerability advisories in transitive packages (for example
  `Microsoft.SemanticKernel.Core`). Report; do not suppress.
- `MSB3246` warnings for native files in the Workflows package. Pre-existing.
- The VS Code `generateDebugSymbols` task may look up a bundle path. Review separately.
