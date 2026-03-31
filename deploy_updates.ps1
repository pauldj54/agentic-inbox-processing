# ============================================================================
# Deploy all resources
# Resources: Logic Apps (email + SFTP) + Web App (Python code)
# Resource Group: rg-docproc-dev
#
# Usage:
#   .\deploy_updates.ps1                   # defaults to -Environment dev
#   .\deploy_updates.ps1 -Environment prod
# ============================================================================
param(
    [ValidateSet("dev", "prod")]
    [string]$Environment = "dev"
)

Write-Host "Deploying with environment: $Environment" -ForegroundColor Cyan

$resourceGroup    = "rg-docproc-dev"
$logicAppName     = "logic-docproc-dev-izr2ch55woa3c"
$sftpLogicAppName = "logic-sftp-docproc-dev-izr2ch55woa3c"
$webAppName       = "app-docproc-dev-izr2ch55woa3c"
$keyVaultName     = "kv-docproc-dev-izr2ch55"

# --- 1a. Deploy Email Logic App workflow ---
Write-Host "`n=== Deploying Email Logic App workflow ===" -ForegroundColor Cyan

# Fetch Graph API client secret from Key Vault (never stored in source control)
Write-Host "Fetching Graph API client secret from Key Vault..." -ForegroundColor Gray
$graphSecret = az keyvault secret show `
  --vault-name $keyVaultName `
  --name "graph-client-secret" `
  --query "value" -o tsv

if (-not $graphSecret) {
    Write-Host "Failed to retrieve graph-client-secret from Key Vault!" -ForegroundColor Red
    Write-Host "Ensure the secret exists in $keyVaultName and you have Key Vault Secrets User role." -ForegroundColor Yellow
} else {
    Write-Host "Graph secret retrieved successfully." -ForegroundColor Gray
}

# Read the workflow definition
$emailWfJson = Get-Content -Raw "logic-apps/email-ingestion/workflow.json" | ConvertFrom-Json -AsHashtable

# Merge environment-specific parameter values
$emailEnvFile = "logic-apps/email-ingestion/parameters.$Environment.json"
if (-not (Test-Path $emailEnvFile)) {
    Write-Host "Parameter file not found: $emailEnvFile" -ForegroundColor Red
    exit 1
}
Write-Host "Merging parameters from $emailEnvFile..." -ForegroundColor Gray
$emailEnvParams = (Get-Content -Raw $emailEnvFile | ConvertFrom-Json -AsHashtable)['parameters']

# Only merge parameters that are declared in the workflow definition
$declaredParams = $emailWfJson['definition']['parameters'].Keys
foreach ($key in $emailEnvParams.Keys) {
    # pollingFrequency/pollingInterval are applied to the trigger, not as Logic App params
    if ($key -in @('pollingFrequency', 'pollingInterval')) { continue }
    if ($key -notin $declaredParams) {
        Write-Host "  Skipping undeclared parameter: $key" -ForegroundColor Yellow
        continue
    }
    $emailWfJson['parameters'][$key] = $emailEnvParams[$key]
}

# Inject the Graph client secret from Key Vault (overrides placeholder in env file)
$emailWfJson['parameters']['graphClientSecret'] = @{ value = $graphSecret }

# Apply polling schedule to the Recurrence trigger (these can't use @parameters() at runtime)
if ($emailEnvParams.ContainsKey('pollingFrequency')) {
    $emailWfJson['definition']['triggers']['Recurrence']['recurrence']['frequency'] = $emailEnvParams['pollingFrequency']['value']
    Write-Host "  Polling frequency: $($emailEnvParams['pollingFrequency']['value'])" -ForegroundColor Gray
}
if ($emailEnvParams.ContainsKey('pollingInterval')) {
    $emailWfJson['definition']['triggers']['Recurrence']['recurrence']['interval'] = $emailEnvParams['pollingInterval']['value']
    Write-Host "  Polling interval:  $($emailEnvParams['pollingInterval']['value'])" -ForegroundColor Gray
}

# Write the full workflow (definition + parameters) to a temp file
$emailFullPath = "$env:TEMP\email-la-full.json"
$emailWfJson | ConvertTo-Json -Depth 50 | Set-Content -Path $emailFullPath -Encoding UTF8

az logic workflow create `
  --resource-group $resourceGroup `
  --name $logicAppName `
  --definition "@$emailFullPath"

# Clean up temp file
Remove-Item $emailFullPath -ErrorAction SilentlyContinue

if ($LASTEXITCODE -eq 0) {
    Write-Host "Email Logic App deployed successfully." -ForegroundColor Green
} else {
    Write-Host "Email Logic App deployment failed!" -ForegroundColor Red
}

# --- 1b. Deploy SFTP Logic App workflow ---
Write-Host "`n=== Deploying SFTP Logic App workflow ===" -ForegroundColor Cyan

# Fetch SharePoint client secret from Key Vault (never stored in source control)
Write-Host "Fetching SharePoint client secret from Key Vault..." -ForegroundColor Gray
$spSecret = az keyvault secret show `
  --vault-name $keyVaultName `
  --name "sharepoint-client-secret" `
  --query "value" -o tsv

if (-not $spSecret) {
    Write-Host "Failed to retrieve sharepoint-client-secret from Key Vault!" -ForegroundColor Red
    Write-Host "Ensure the secret exists in $keyVaultName and you have Key Vault Secrets User role." -ForegroundColor Yellow
} else {
    Write-Host "Secret retrieved successfully." -ForegroundColor Gray
}

# Read the workflow definition
$wfJson = Get-Content -Raw "logic-apps/sftp-file-ingestion/workflow.json" | ConvertFrom-Json -AsHashtable

# Merge environment-specific parameter values
$sftpEnvFile = "logic-apps/sftp-file-ingestion/parameters.$Environment.json"
if (-not (Test-Path $sftpEnvFile)) {
    Write-Host "Parameter file not found: $sftpEnvFile" -ForegroundColor Red
    exit 1
}
Write-Host "Merging parameters from $sftpEnvFile..." -ForegroundColor Gray
$sftpEnvParams = (Get-Content -Raw $sftpEnvFile | ConvertFrom-Json -AsHashtable)['parameters']

# Only merge parameters that are declared in the workflow definition
$sftpDeclaredParams = $wfJson['definition']['parameters'].Keys
foreach ($key in $sftpEnvParams.Keys) {
    if ($key -notin $sftpDeclaredParams) {
        Write-Host "  Skipping undeclared parameter: $key" -ForegroundColor Yellow
        continue
    }
    $wfJson['parameters'][$key] = $sftpEnvParams[$key]
}

# Inject the SharePoint client secret from Key Vault (overrides placeholder in env file)
$wfJson['parameters']['sharepointClientSecret'] = @{ value = $spSecret }

# Write the full workflow (definition + parameters) to a temp file
$fullPath = "$env:TEMP\sftp-la-full.json"
$wfJson | ConvertTo-Json -Depth 50 | Set-Content -Path $fullPath -Encoding UTF8

az logic workflow create `
  --resource-group $resourceGroup `
  --name $sftpLogicAppName `
  --definition "@$fullPath"

# Clean up temp file
Remove-Item $fullPath -ErrorAction SilentlyContinue

if ($LASTEXITCODE -eq 0) {
    Write-Host "SFTP Logic App deployed successfully." -ForegroundColor Green
} else {
    Write-Host "SFTP Logic App deployment failed!" -ForegroundColor Red
}

# --- 2. Deploy Web App (agent + dashboard code) ---
Write-Host "`n=== Deploying Web App code ===" -ForegroundColor Cyan

# Ensure the Web App is started before deploying
Write-Host "Starting Web App..." -ForegroundColor Gray
az webapp start --resource-group $resourceGroup --name $webAppName 2>$null

# Verify the app is actually running (QuotaExceeded on Free tier can block deploys)
$appState = az webapp show --resource-group $resourceGroup --name $webAppName --query "state" -o tsv
if ($appState -ne "Running") {
    Write-Host "Web App state is '$appState'. Attempting restart..." -ForegroundColor Yellow
    az webapp restart --resource-group $resourceGroup --name $webAppName 2>$null
    Start-Sleep -Seconds 10
    $appState = az webapp show --resource-group $resourceGroup --name $webAppName --query "state" -o tsv
    if ($appState -ne "Running") {
        Write-Host "Web App still not running (state: $appState). Deployment will likely fail." -ForegroundColor Red
        Write-Host "If on Free tier (F1), the daily quota may be exceeded. Scale to B1 or wait for reset." -ForegroundColor Yellow
    }
}
Write-Host "Web App state: $appState" -ForegroundColor Gray

# Zip the application code (exclude non-deployment files)
$zipPath = "$env:TEMP\webapp-deploy.zip"
if (Test-Path $zipPath) { Remove-Item $zipPath }

Compress-Archive -Path @(
    "src",
    "utils",
    "requirements.txt",
    "startup.sh"
) -DestinationPath $zipPath -Force

# Use --async to avoid blocking on slow site startup
az webapp deploy `
  --resource-group $resourceGroup `
  --name $webAppName `
  --src-path $zipPath `
  --type zip `
  --async true

if ($LASTEXITCODE -eq 0) {
    Write-Host "Web App deployed successfully." -ForegroundColor Green
} else {
    Write-Host "Web App deployment failed!" -ForegroundColor Red
}

# --- 3. Add required app settings ---
Write-Host "`n=== Ensuring required app settings ===" -ForegroundColor Cyan

# Discover resource endpoints from resource group
$storageAccount = az resource list -g $resourceGroup `
  --query "[?type=='Microsoft.Storage/StorageAccounts' && !starts_with(name, 'sftp')].name | [0]" -o tsv

$sbNamespace = az resource list -g $resourceGroup `
  --query "[?type=='Microsoft.ServiceBus/namespaces'].name | [0]" -o tsv

$cosmosAccount = az resource list -g $resourceGroup `
  --query "[?type=='Microsoft.DocumentDB/databaseAccounts'].name | [0]" -o tsv

$diAccount = az resource list -g $resourceGroup `
  --query "[?type=='Microsoft.CognitiveServices/accounts' && starts_with(name, 'di-')].name | [0]" -o tsv

# Read AI endpoint from local .env01 (not discoverable from Azure resources)
$aiEndpoint = ""
$aiModel = "gpt-4o"
if (Test-Path ".env01") {
    $envLines = Get-Content ".env01"
    $match = $envLines | Where-Object { $_ -match '^AZURE_AI_PROJECT_ENDPOINT=' }
    if ($match) { $aiEndpoint = ($match -split '=', 2)[1] }
    $modelMatch = $envLines | Where-Object { $_ -match '^AZURE_AI_MODEL_DEPLOYMENT_NAME=' }
    if ($modelMatch) { $aiModel = ($modelMatch -split '=', 2)[1] }
}

az webapp config appsettings set `
  --resource-group $resourceGroup `
  --name $webAppName `
  --settings `
    "STORAGE_ACCOUNT_URL=https://$storageAccount.blob.core.windows.net" `
    "KEY_VAULT_URL=https://$keyVaultName.vault.azure.net/" `
    "KEY_VAULT_NAME=$keyVaultName" `
    "GRAPH_CLIENT_ID=93350d2a-45d4-4bb0-bd21-5438c2f6cc7f" `
    "GRAPH_TENANT_ID=2ce91bb1-0177-45b5-a98c-9c2f7ebe64de" `
    "SERVICEBUS_NAMESPACE=$sbNamespace" `
    "SERVICEBUS_QUEUE_NAME=intake" `
    "COSMOS_ENDPOINT=https://${cosmosAccount}.documents.azure.com:443/" `
    "COSMOS_DATABASE=email-processing" `
    "DOCUMENT_INTELLIGENCE_ENDPOINT=https://${diAccount}.cognitiveservices.azure.com/" `
    "PIPELINE_MODE=triage-only" `
    "TRIAGE_COMPLETE_QUEUE=triage-complete" `
    "AZURE_AI_PROJECT_ENDPOINT=$aiEndpoint" `
    "AZURE_AI_MODEL_DEPLOYMENT_NAME=$aiModel" `
  -o none

if ($LASTEXITCODE -eq 0) {
    Write-Host "App settings configured." -ForegroundColor Green
} else {
    Write-Host "Failed to configure app settings!" -ForegroundColor Red
}

# --- 4. Ensure webapp MI has required RBAC roles ---
Write-Host "`n=== Ensuring RBAC for webapp ===" -ForegroundColor Cyan

$webAppPrincipalId = az webapp identity show `
  --resource-group $resourceGroup `
  --name $webAppName `
  --query principalId -o tsv

$kvId = az keyvault show --name $keyVaultName --query id -o tsv

az role assignment create `
  --role "Key Vault Secrets User" `
  --assignee-object-id $webAppPrincipalId `
  --assignee-principal-type ServicePrincipal `
  --scope $kvId 2>$null

Write-Host "Key Vault Secrets User role ensured." -ForegroundColor Gray

# Agent needs to SEND messages to triage-complete, discarded, human-review, archival-pending
$sbId = az resource list -g $resourceGroup `
  --query "[?type=='Microsoft.ServiceBus/namespaces'].id | [0]" -o tsv

az role assignment create `
  --role "Azure Service Bus Data Sender" `
  --assignee-object-id $webAppPrincipalId `
  --assignee-principal-type ServicePrincipal `
  --scope $sbId 2>$null

Write-Host "Service Bus Data Sender role ensured." -ForegroundColor Gray

Write-Host "`n=== Deployment complete ===" -ForegroundColor Green
Write-Host "Logic App: $logicAppName"
Write-Host "Web App:   https://$webAppName.azurewebsites.net"