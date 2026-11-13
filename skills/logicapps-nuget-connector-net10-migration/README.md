# Logic Apps NuGet connector .NET 10 migration skill

Migrates a NuGet-based Azure Logic Apps Standard project with a custom built-in connector
from .NET 8 to .NET 10, using configuration changes only.

For custom-code (local function) workspaces, use `logicapps-custom-code-net10-migration`
instead. The two migrations have opposite extension bundle and worker runtime rules.

## Install

Copy this entire folder into a skills directory:

- Personal: `%USERPROFILE%\.copilot\skills\logicapps-nuget-connector-net10-migration`
- Project: `.github\skills\logicapps-nuget-connector-net10-migration`

No third-party Python packages are required. Licensed under MIT.

## Prerequisites

- .NET SDK 10.0.403 or later.
- Core Tools 4.16.0 or later for local runs (main host on .NET 10).
- Python 3.11+ for the helper and its tests.

## Use

Ask the agent:

> Migrate this NuGet-based Logic App and its custom connector to .NET 10 using
> logicapps-nuget-connector-net10-migration.

Or run the helper directly:

```powershell
python ".\scripts\migrate.py" --root "C:\src\MySolution"
python ".\scripts\migrate.py" --root "C:\src\MySolution" --connector-version 2.0.0 --apply
```

The default is a dry run. Exit `0` means a valid plan or a successful apply, not a passing
build. Exit `1` means a blocker. Use `--project` to select a host when several exist.

The helper prints file names and change descriptions, never settings values. It performs
no restore, build, host start, or Azure operation. After applying, it lists the Azure
app-setting changes you still need to make:

- Delete `AzureFunctionsJobHost__extensionBundle__id` and `__version`.
- Delete `LOGICAPP_INPROC_REDIRECT` if present.
- Keep `FUNCTIONS_WORKER_RUNTIME=dotnet`.

## Test

```powershell
python -m unittest discover -s ".\tests" -v
```

The tests use synthetic projects. The helper was also checked against a real .NET 8
sample: its output matched an independently migrated branch, the solution built with
0 errors, and 30 of 30 unit tests passed. The deployed app ran on .NET 10 after the
Azure prerequisites were applied.
