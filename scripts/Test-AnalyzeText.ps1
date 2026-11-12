#requires -Version 7.0
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateRange(1, 65535)]
    [int] $Port
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$ProgressPreference = 'SilentlyContinue'

function Invoke-AzJson {
    param([string] $Operation, [string[]] $Arguments)

    try {
        $output = @(& az @Arguments 2>&1)
        $exitCode = $LASTEXITCODE
    }
    catch {
        throw "$Operation could not be executed."
    }

    $text = (@($output | Where-Object { $_ -is [string] }) -join "`n")
    try {
        $data = ConvertFrom-Json -InputObject $text -AsHashtable -ErrorAction Stop
    }
    catch {
        throw "$Operation returned no usable JSON (Azure CLI exit $exitCode)."
    }
    if ($data -isnot [System.Collections.IDictionary]) {
        throw "$Operation returned no usable JSON (Azure CLI exit $exitCode)."
    }

    return [pscustomobject]@{ ExitCode = $exitCode; Data = $data }
}

function Invoke-JsonPost {
    param([uri] $Uri, [string] $Body, [string] $Description)

    try {
        return Invoke-WebRequest -Uri $Uri -Method Post -ContentType 'application/json' -Body $Body `
            -NoProxy -MaximumRedirection 0 -SkipHttpErrorCheck -TimeoutSec 120 `
            -ErrorAction Stop -WarningAction SilentlyContinue -InformationAction SilentlyContinue `
            -Verbose:$false -Debug:$false
    }
    catch {
        throw "$Description request failed; no retry was attempted."
    }
}

function Read-JsonObject {
    param($Response, [string] $Description)

    $content = $Response.Content
    if ($content -is [byte[]]) {
        $content = [System.Text.Encoding]::UTF8.GetString($content)
    }
    try {
        $data = ConvertFrom-Json -InputObject ([string] $content) -AsHashtable -ErrorAction Stop
    }
    catch {
        throw "$Description returned invalid JSON."
    }
    if ($data -isnot [System.Collections.IDictionary]) {
        throw "$Description did not return a JSON object."
    }
    return $data
}

function Get-SafeStatus {
    param($Status)

    if ($Status -is [string] -and $Status -cmatch '^[A-Za-z]{1,32}$') {
        return $Status
    }
    return 'unknown'
}

function Assert-RunSucceeded {
    param([string] $RunId, [string] $Description)

    $wait = Invoke-AzJson -Operation 'Run wait' -Arguments @(
        'logicapp', 'run', 'wait', '--local', '--port', "$Port",
        '--workflow', 'AnalyzeText', '--run-id', $RunId,
        '--timeout', '120', '--interval', '2', '--output', 'json', '--only-show-errors'
    )
    $runStatus = Get-SafeStatus $wait.Data['status']
    if ($wait.ExitCode -ne 0 -or $runStatus -cne 'Succeeded' -or
        $wait.Data['runId'] -cne $RunId -or $wait.Data['workflow'] -cne 'AnalyzeText' -or
        $wait.Data['workflowKind'] -cne 'Stateful' -or $wait.Data['terminal'] -isnot [bool] -or
        -not $wait.Data['terminal']) {
        throw "$Description run was not observed Succeeded (CLI exit $($wait.ExitCode), status $runStatus)."
    }

    $action = Invoke-AzJson -Operation 'Action show' -Arguments @(
        'logicapp', 'run', 'action', 'show', '--local', '--port', "$Port",
        '--workflow', 'AnalyzeText', '--run-id', $RunId, '--action', 'Analyze_text',
        '--output', 'json', '--only-show-errors'
    )
    $actionStatus = Get-SafeStatus $action.Data['status']
    if ($action.ExitCode -ne 0 -or $actionStatus -cne 'Succeeded' -or
        $action.Data['runId'] -cne $RunId -or $action.Data['workflow'] -cne 'AnalyzeText' -or
        $action.Data['action'] -cne 'Analyze_text') {
        throw "$Description Analyze_text action was not observed Succeeded (CLI exit $($action.ExitCode), status $actionStatus)."
    }
}

function Assert-ExplicitError {
    param([System.Collections.IDictionary] $Body, [string] $Description)

    if ($Body.Contains('normalizedText') -or $Body.Contains('uppercaseText') -or $Body.Contains('characterCount')) {
        throw "$Description returned success-shaped JSON for invalid input."
    }
    $errorValue = $Body['error']
    if ($errorValue -is [string] -and -not [string]::IsNullOrWhiteSpace($errorValue)) {
        return
    }
    if ($errorValue -is [System.Collections.IDictionary]) {
        foreach ($key in @('code', 'message')) {
            if ($errorValue[$key] -is [string] -and -not [string]::IsNullOrWhiteSpace($errorValue[$key])) {
                return
            }
        }
    }
    if ($Body['message'] -is [string] -and -not [string]::IsNullOrWhiteSpace($Body['message'])) {
        return
    }
    throw "$Description did not return an explicit JSON error."
}

$extension = Invoke-AzJson -Operation 'Logic Apps extension check' -Arguments @(
    'extension', 'show', '--name', 'logicapp', '--output', 'json', '--only-show-errors'
)
if ($extension.ExitCode -ne 0 -or $extension.Data['version'] -cne '1.0.0b1') {
    throw 'This harness requires the reviewed Azure CLI logicapp extension 1.0.0b1.'
}

$runtime = Invoke-AzJson -Operation 'Runtime status' -Arguments @(
    'logicapp', 'runtime', 'show', '--port', "$Port", '--output', 'json', '--only-show-errors'
)
$readiness = $runtime.Data['readiness']
if ($runtime.ExitCode -ne 0 -or $runtime.Data['port'] -ne $Port -or
    $runtime.Data['state'] -ne 'running' -or $runtime.Data['startedByCli'] -isnot [bool] -or
    -not $runtime.Data['startedByCli'] -or $readiness -isnot [System.Collections.IDictionary] -or
    $readiness['managementEndpointReachable'] -isnot [bool] -or -not $readiness['managementEndpointReachable']) {
    throw 'The selected port does not have a ready, CLI-owned runtime record.'
}

$recordedProject = $runtime.Data['projectPath']
if ($recordedProject -isnot [string] -or -not [System.IO.Path]::IsPathFullyQualified($recordedProject)) {
    throw 'The runtime record does not identify an absolute project path.'
}
try {
    $expectedProject = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\LogicApp\bin\Debug\net10.0')).ProviderPath
    $actualProject = (Resolve-Path -LiteralPath $recordedProject).ProviderPath
}
catch {
    throw 'The expected or recorded runtime project path could not be resolved.'
}
$separators = [char[]] @([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar)
if (-not [string]::Equals($expectedProject.TrimEnd($separators), $actualProject.TrimEnd($separators), [StringComparison]::OrdinalIgnoreCase)) {
    throw 'The runtime record belongs to a different project; no workflow was invoked.'
}

$managementUri = [uri] ("http://127.0.0.1:{0}/runtime/webhooks/workflow/api/management/workflows/AnalyzeText/triggers/manual/listCallbackUrl?api-version=2018-11-01" -f $Port)
$discovery = Invoke-JsonPost -Uri $managementUri -Body '{}' -Description 'Callback discovery'
if ($discovery.StatusCode -ne 200) {
    throw "Callback discovery returned HTTP $($discovery.StatusCode), not 200."
}
$callback = Read-JsonObject -Response $discovery -Description 'Callback discovery'
if ($callback['method'] -and $callback['method'] -ine 'POST') {
    throw 'The discovered trigger does not use POST.'
}
$callbackUri = $null
if ($callback['value'] -isnot [string] -or
    -not [uri]::TryCreate($callback['value'], [UriKind]::Absolute, [ref] $callbackUri) -or
    $callbackUri.Scheme -cne 'http' -or -not $callbackUri.IsLoopback -or
    $callbackUri.Port -ne $Port -or $callbackUri.UserInfo -or $callbackUri.Fragment) {
    throw 'The callback is not an HTTP loopback URL on the selected port.'
}

$validCases = @(
    @{ Name = 'sample'; Text = '  Hello Logic Apps  '; Normalized = 'Hello Logic Apps'; Uppercase = 'HELLO LOGIC APPS'; Count = 16 }
    @{ Name = 'Unicode'; Text = '  Hi 😀  '; Normalized = 'Hi 😀'; Uppercase = 'HI 😀'; Count = 5 }
    @{ Name = 'limit boundary'; Text = ('a' * 100000); Normalized = ('a' * 100000); Uppercase = ('A' * 100000); Count = 100000 }
)
foreach ($case in $validCases) {
    $json = ConvertTo-Json -InputObject @{ text = $case.Text } -Compress
    $response = Invoke-JsonPost -Uri $callbackUri -Body $json -Description $case.Name
    if ($response.StatusCode -ne 200) {
        throw "$($case.Name) returned HTTP $($response.StatusCode), not 200."
    }
    $body = Read-JsonObject -Response $response -Description $case.Name
    $count = $body['characterCount']
    if ($body['normalizedText'] -isnot [string] -or $body['normalizedText'] -cne $case.Normalized -or
        $body['uppercaseText'] -isnot [string] -or $body['uppercaseText'] -cne $case.Uppercase -or
        ($count -isnot [int] -and $count -isnot [long]) -or $count -ne $case.Count) {
        throw "$($case.Name) returned incorrect JSON output."
    }

    $headerNames = @($response.Headers.Keys | Where-Object { $_ -ieq 'x-ms-workflow-run-id' })
    if ($headerNames.Count -ne 1) {
        throw "$($case.Name) response has no unique x-ms-workflow-run-id header; run verification is unavailable."
    }
    $runIds = @($response.Headers[$headerNames[0]])
    if ($runIds.Count -ne 1 -or $runIds[0] -isnot [string] -or
        [string]::IsNullOrWhiteSpace($runIds[0]) -or $runIds[0].Trim() -cnotmatch '^[A-Za-z0-9._-]+$') {
        throw "$($case.Name) response has no unique usable run ID; run verification is unavailable."
    }
    Assert-RunSucceeded -RunId $runIds[0].Trim() -Description $case.Name
    Write-Output "PASS $($case.Name): HTTP 200; run and Analyze_text succeeded."
}

$invalidCases = @(
    @{ Name = 'missing'; Payload = @{} }
    @{ Name = 'null'; Payload = @{ text = $null } }
    @{ Name = 'non-string'; Payload = @{ text = 42 } }
    @{ Name = 'empty'; Payload = @{ text = '' } }
    @{ Name = 'whitespace'; Payload = @{ text = " `t`r`n" } }
    @{ Name = 'over limit'; Payload = @{ text = ('a' * 100001) }; ErrorCode = 'TextTooLong' }
)
foreach ($case in $invalidCases) {
    $json = ConvertTo-Json -InputObject $case.Payload -Compress
    $response = Invoke-JsonPost -Uri $callbackUri -Body $json -Description $case.Name
    if ($response.StatusCode -ne 400) {
        throw "$($case.Name) returned HTTP $($response.StatusCode), not 400."
    }
    $body = Read-JsonObject -Response $response -Description $case.Name
    Assert-ExplicitError -Body $body -Description $case.Name
    if ($case.ContainsKey('ErrorCode') -and $body['error']['code'] -cne $case.ErrorCode) {
        throw "$($case.Name) did not return the expected error code."
    }
    Write-Output "PASS $($case.Name): HTTP 400 with a JSON error."
}
