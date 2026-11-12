# Logic Apps custom built-in connector sample (.NET 10)

A test bed migrated from a .NET 8 Azure Logic Apps Standard custom built-in connector to .NET 10. The app, connector, and tests now target `net10.0`. It is not a production connector.

## Contents

| Path | Purpose |
|------|---------|
| `TextToolsConnector` | .NET 10 class library, version 2.0.0. Service provider `/serviceProviders/textTools` with one action, `analyzeText`. |
| `TextToolsConnector.Tests` | xUnit tests for the provider (30 tests). |
| `LogicApp` | NuGet-based Logic Apps Standard project. References the connector project. Contains the Stateful workflow `AnalyzeText`. |
| `scripts\Test-AnalyzeText.ps1` | Optional live checks against a running local host. |
| `CustomConnectorSample.code-workspace` | VS Code multi-root workspace. |

## Current versions

- .NET SDK 10.0.404 (pinned in both `global.json` files), target `net10.0`
- `Microsoft.NET.Sdk.Functions` 4.6.0
- `Microsoft.Azure.Workflows.WebJobs.Extension` 1.194.31 (host and connector)
- Connector dependency pins: `Microsoft.Extensions.DependencyInjection.Abstractions` 10.0.0 and `Microsoft.Extensions.Primitives` 9.0.9, resolving the actual `NU1605` conflicts in this dependency graph.
- `host.json` has no extension bundle. The workflow runtime loads from NuGet.

The connector retains its existing `IWebJobsStartup`, extension configuration,
and `IServiceOperationsProvider` registration. No isolated Functions `Program.cs`
or custom-code worker project was added, and workflow business logic is unchanged.
The original .NET 8 baseline is available at commit
`29cb20e281b7107971a63d58e6a5d2d4030d62a5`.

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

`-p:EnableProjectDataOnBuild=false` retains the baseline's C# Dev Kit build workaround. The VS Code tasks already pass it.

Expected warnings: `MSB3246` (native asset in the workflow package) and `NETSDK1188` (ICU4N locale resources).

NuGet also reports upstream dependency vulnerability advisories, including a
critical advisory for `Microsoft.SemanticKernel.Core` 1.61.0 and high-severity
advisories for several other transitive dependencies. These warnings are not
suppressed or remediated by this framework migration. Treat dependency remediation
as separate work before considering production use.

## Run locally

Use a .NET 10-capable Logic Apps local host and the SDK selected by this repository.
Installing SDK 10 alone does not upgrade the runtime used by Core Tools.
The migration was validated with local unit tests and Azure workflow execution;
the local live-test harness was updated for the new path but was not executed
as part of this migration.

1. Start Azurite on the ports set in `LogicApp\local.settings.json`.
2. Open `CustomConnectorSample.code-workspace` and start debugging the `LogicApp` folder, or run the build task, then `func host start --address 127.0.0.1` from `LogicApp\bin\Debug\net10.0`.
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

The workflow validates `text` first and returns 400 for a missing, blank, null,
or non-string value. Connector failures return a generic 500 and terminate the
run as Failed. Success and these four input-validation paths were verified in
Azure before and after migration; other connector failure paths were not
exercised as part of this migration.

## Azure deployment

The unmodified .NET 8 application baseline at commit
`29cb20e281b7107971a63d58e6a5d2d4030d62a5` was built and deployed on November 12, 2026.

| Resource | Name |
|----------|------|
| Resource group | `pravagarnugetlogicapps` |
| Logic App | `pravagartexttoolsnet8` |
| Dedicated storage account | `pravagartexttoolsnet8` |
| Content share in that account | `pravagartexttoolsnet8` |
| Existing shared compute plan | `pravagarnugetlogicapp` |

[infra/baseline.json](infra/baseline.json) provisions a dedicated storage account
for this app and uses it for both `AzureWebJobsStorage` and
`WEBSITE_CONTENTAZUREFILECONNECTIONSTRING`. Each app must have its own storage
account. The storage account name defaults to the app name; supply a distinct,
valid storage account name when needed. The template creates no new compute plan.
Storage keys are resolved by Azure Resource Manager rather than stored in source.
Shared-key access is retained for this baseline's Azure Files content connection.

The resource names retain their `net8` suffix to preserve the existing app and
its dedicated storage; they do not indicate the current target framework.

### Verified .NET 10 migration

- All three projects target `net10.0`; Release publish succeeded with SDK 10.0.404.
- All 30 connector unit tests passed on .NET 10.
- Six Azure HTTP cases passed: trimming/uppercasing, internal whitespace preservation,
  and rejection of missing, blank, null, and non-string text.
- Run `08584070910018236294984546884CU00` recorded successful `Analyze_text` execution
  after migration.
- Production worker PID 5592 loaded CoreCLR 10, Functions host 4.1055.300.26527,
  `TextToolsConnector.dll` 2.0.0.0, and workflow runtime 1.194.31.0.
- `LOGICAPP_INPROC_REDIRECT` is absent, allowing the platform's out-of-process
  host to run the app. It was also removed from the infrastructure template.
- `FUNCTIONS_INPROC_NET8_ENABLED=1` and `FUNCTIONS_WORKER_RUNTIME=dotnet` were
  preserved; these settings do not identify the actual CLR. The configured
  `netFrameworkVersion` label remains `v8.0`; the loaded modules above verify
  the actual host.
- Both runtime and content storage remain in dedicated account `pravagartexttoolsnet8`.
- Latest code deployment: `cf41058b773648ad85d33f6a14001093`.
- Publish output and VS Code deploy path: `LogicApp\bin\Release\net10.0\publish`.

The app was stopped before removing the redirect setting and deploying .NET 10
content. It was started after the deployment completed successfully.

### Baseline rollback

The previously verified .NET 8 publish output was preserved as
`LogicApp\bin\rollback\TextTools-net8-baseline-031186bb37ad423fa7560847e34f2e68.zip`.
To roll back, stop the app, deploy that complete baseline package without an
intermediate restart, restore `LOGICAPP_INPROC_REDIRECT=1`, and start the app.
Verify workflows before resuming use. The flag alone cannot make the .NET 10
connector run on the legacy .NET 8 host.

The baseline had been verified with CoreCLR 8, Functions host 4.852.200.26454,
connector 1.0.0.0, workflow runtime 1.165.50.0, and all 30 unit/six Azure HTTP tests.

The initial deployment incorrectly reused the `pravagarnugetapp1` storage account.
This was corrected by stopping the new app, provisioning dedicated storage,
switching both storage connections, and redeploying its content. The original
account, its earlier test history/content share, and the original Logic App were
left untouched. Earlier test-run history was not migrated to the dedicated account.

## Sources

- [Create custom built-in connectors for Standard logic apps](https://learn.microsoft.com/en-us/azure/logic-apps/create-custom-built-in-connector-standard)
- [Built-in connector extensibility model](https://learn.microsoft.com/en-us/azure/logic-apps/custom-connector-overview#built-in-connector-extensibility-model)

The Learn tutorial targets .NET Core 3.1 and a Cosmos DB trigger, and installs into the shared extension bundle. This sample adapts it to an action-only connector registered through a project reference, initially on .NET 8 and now on .NET 10.
