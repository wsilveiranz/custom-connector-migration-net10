# Logic Apps custom built-in connector sample (.NET 8)

A local test bed for migrating an Azure Logic Apps Standard custom built-in connector from .NET 8 to .NET 10. It is not a production connector.

## Contents

| Path | Purpose |
|------|---------|
| `TextToolsConnector` | .NET 8 class library. Service provider `/serviceProviders/textTools` with one action, `analyzeText`. |
| `TextToolsConnector.Tests` | xUnit tests for the provider (30 tests). |
| `LogicApp` | NuGet-based Logic Apps Standard project. References the connector project. Contains the Stateful workflow `AnalyzeText`. |
| `scripts\Test-AnalyzeText.ps1` | Optional live checks against a running local host. |
| `CustomConnectorSample.code-workspace` | VS Code multi-root workspace. |

## Baseline versions

- .NET SDK 8.0.427 (pinned in `global.json`), target `net8.0`
- `Microsoft.NET.Sdk.Functions` 4.4.0
- `Microsoft.Azure.Workflows.WebJobs.Extension` 1.165.50 (host and connector)
- Azure Functions Core Tools 4.16.0
- `host.json` has no extension bundle. The workflow runtime loads from NuGet.

.NET 8 reached end of support on November 10, 2026. This baseline exists only to test the migration.

## Connector behavior

`analyzeText` takes `text` and returns:

- `normalizedText`: input with leading and trailing whitespace removed
- `uppercaseText`: `normalizedText` in invariant-culture uppercase
- `characterCount`: UTF-16 code units in `normalizedText`

The runtime rejects a service provider connection with no parameter values, so the connector defines one non-secret setting, `maxTextLength` (default `100000`). `LogicApp\connections.json` sets it.

## Build and test

```powershell
dotnet build LogicApp\LogicApp.csproj -p:EnableProjectDataOnBuild=false
dotnet test TextToolsConnector.Tests\TextToolsConnector.Tests.csproj -p:EnableProjectDataOnBuild=false
az logicapp workflow validate --path LogicApp --all
```

`-p:EnableProjectDataOnBuild=false` avoids an MSB4062 error caused by C# Dev Kit loading an SDK 10 task into an SDK 8 build. The VS Code tasks already pass it.

Expected warnings: `MSB3246` (native asset in the workflow package) and `NETSDK1188` (ICU4N locale resources).

## Run locally

1. Start Azurite on the ports set in `LogicApp\local.settings.json`.
2. Open `CustomConnectorSample.code-workspace` and start debugging the `LogicApp` folder, or run the build task, then `func host start --address 127.0.0.1` from `LogicApp\bin\Debug\net8.0`.
3. Confirm the connector loaded:

   ```powershell
   az logicapp connector show --local --port 7071 --connector /serviceProviders/textTools
   ```

4. Get the callback URL for `AnalyzeText` / trigger `manual` from the designer or the `listCallbackUrl` management API, then post:

   ```json
   { "text": "  Hello Logic Apps  " }
   ```

   Expected HTTP 200:

   ```json
   {
     "normalizedText": "Hello Logic Apps",
     "uppercaseText": "HELLO LOGIC APPS",
     "characterCount": 16
   }
   ```

The workflow validates `text` first and returns 400 for a missing or blank value. Connector failures return a generic 500 and terminate the run as Failed. Only the success path was verified at runtime.

## Sources

- [Create custom built-in connectors for Standard logic apps](https://learn.microsoft.com/en-us/azure/logic-apps/create-custom-built-in-connector-standard)
- [Built-in connector extensibility model](https://learn.microsoft.com/en-us/azure/logic-apps/custom-connector-overview#built-in-connector-extensibility-model)

The Learn tutorial targets .NET Core 3.1 and a Cosmos DB trigger, and installs into the shared extension bundle. This sample adapts it to an action-only .NET 8 connector registered through a project reference.
