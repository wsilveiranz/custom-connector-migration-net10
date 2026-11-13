# Logic Apps custom built-in connector sample (.NET 10)

A local test bed for an Azure Logic Apps Standard custom built-in connector migrated from .NET 8 to .NET 10. It is not a production connector.

## Contents

| Path | Purpose |
|------|---------|
| `TextToolsConnector` | .NET 10 class library. Service provider `/serviceProviders/textTools` with one action, `analyzeText`. |
| `TextToolsConnector.Tests` | xUnit tests for the provider (30 tests). |
| `LogicApp` | NuGet-based Logic Apps Standard project. References the connector project. Contains the Stateful workflow `AnalyzeText`. |
| `scripts\Test-AnalyzeText.ps1` | Optional live checks against a running local host. |
| `CustomConnectorSample.code-workspace` | VS Code multi-root workspace. |

## Versions

- .NET SDK 10.0.403 (pinned in both `global.json` files), target `net10.0`
- `Microsoft.NET.Sdk.Functions` 4.4.0
- `Microsoft.Azure.Workflows.WebJobs.Extension` 1.194.31 (host and connector)
- Azure Functions Core Tools 4.16.0 was tested locally
- `host.json` has no extension bundle. The workflow runtime loads from NuGet.

The connector directly references `Microsoft.Extensions.DependencyInjection.Abstractions` 10.0.0 and `Microsoft.Extensions.Primitives` 9.0.9 to resolve dependency downgrades in this package graph. Restore also reports high and critical vulnerabilities in transitive packages. Review those advisories before using this dependency graph outside the sample.

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

The VS Code tasks also set `EnableProjectDataOnBuild=false`.

The build reports `MSB3246` and `NETSDK1188` warnings as well as NuGet vulnerability advisories.

## Run locally

The .NET 10 app requires `FUNCTIONS_WORKER_RUNTIME=dotnet-isolated`. With `dotnet`, Core Tools selects its .NET 8 in-process host, which cannot load the `net10.0` connector or the dependencies the .NET 10 build expects the shared framework to provide. With `dotnet-isolated`, Core Tools 4.16.0 uses its .NET 10 host, which loads the Workflows extension and the connector. Core Tools 4.12.0 does not work: its main host runs on .NET 8. If `func` on your `PATH` is older, put the VS Code managed copy (`%USERPROFILE%\.azurelogicapps\dependencies\FuncCoreTools`) first.

1. Configure the Git-ignored `LogicApp\local.settings.json` with the project's usual local settings, `AzureWebJobsStorage=UseDevelopmentStorage=true`, and `FUNCTIONS_WORKER_RUNTIME=dotnet-isolated`. Start Azurite on its configured ports.
2. Build the project, then run `func host start --address 127.0.0.1` from `LogicApp\bin\Debug\net10.0`, using Core Tools 4.16.0. The VS Code build and publish paths are updated; F5 and the designer were not tested.
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

In an exploratory host configuration, the workflow returned 400 for missing, null, non-string, empty, or whitespace input. An over-limit value returned a generic 500 instead of the expected 400; the .NET 8 baseline behaved the same way.

`cloud.settings.json` retains the .NET 8 baseline and is not validated for .NET 10; it is a template, not deployed app configuration. Preserve any existing `LOGICAPP_INPROC_REDIRECT=1` until the deployed app is tested separately.

## Sources

- [Create custom built-in connectors for Standard logic apps](https://learn.microsoft.com/en-us/azure/logic-apps/create-custom-built-in-connector-standard)
- [Built-in connector extensibility model](https://learn.microsoft.com/en-us/azure/logic-apps/custom-connector-overview#built-in-connector-extensibility-model)

The Learn tutorial targets .NET Core 3.1 and a Cosmos DB trigger, and installs into the shared extension bundle. This sample uses an action-only .NET 10 connector registered through a project reference.
