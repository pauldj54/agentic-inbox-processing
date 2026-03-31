param()
$ErrorActionPreference = "Stop"
$rg = "rg-docproc-dev"
$la = "logic-docproc-dev-izr2ch55woa3c"
$kv = "kv-docproc-dev-izr2ch55"

$graphSecret = az keyvault secret show --vault-name $kv --name "graph-client-secret" --query "value" -o tsv
Write-Host "Secret: $($graphSecret.Length) chars"

$wf = Get-Content -Raw "logic-apps/email-ingestion/workflow.json" | ConvertFrom-Json -AsHashtable
$envParams = (Get-Content -Raw "logic-apps/email-ingestion/parameters.dev.json" | ConvertFrom-Json -AsHashtable)['parameters']
$declared = $wf['definition']['parameters'].Keys
foreach ($key in $envParams.Keys) {
    if ($key -in @('pollingFrequency','pollingInterval')) { continue }
    if ($key -notin $declared) { continue }
    $wf['parameters'][$key] = $envParams[$key]
}
$wf['parameters']['graphClientSecret'] = @{ value = $graphSecret }
if ($envParams.ContainsKey('pollingFrequency')) {
    $wf['definition']['triggers']['Recurrence']['recurrence']['frequency'] = $envParams['pollingFrequency']['value']
}
if ($envParams.ContainsKey('pollingInterval')) {
    $wf['definition']['triggers']['Recurrence']['recurrence']['interval'] = $envParams['pollingInterval']['value']
}
$tmp = "$env:TEMP\email-la-deploy.json"
$wf | ConvertTo-Json -Depth 50 | Set-Content -Path $tmp -Encoding UTF8

Write-Host "Deploying Email Logic App..."
az logic workflow create --resource-group $rg --name $la --definition "@$tmp" -o none 2>&1
$code = $LASTEXITCODE
Remove-Item $tmp -ErrorAction SilentlyContinue
if ($code -eq 0) { Write-Host "SUCCESS" -ForegroundColor Green } else { Write-Host "FAILED (exit $code)" -ForegroundColor Red }
