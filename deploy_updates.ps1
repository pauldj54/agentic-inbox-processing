# ============================================================================
# Deploy Download-Link Intake feature (001)
# Resources: Logic App (workflow.json) + Web App (Python code)
# Resource Group: rg-docproc-dev
# ============================================================================

$resourceGroup = "rg-docproc-dev"
$logicAppName  = "logic-docproc-dev-izr2ch55woa3c"
$webAppName    = "app-docproc-dev-izr2ch55woa3c"

# --- 1. Deploy Logic App workflow (attachmentPaths schema change) ---
Write-Host "`n=== Deploying Logic App workflow ===" -ForegroundColor Cyan

az logic workflow create `
  --resource-group $resourceGroup `
  --name $logicAppName `
  --definition "@logic-apps/email-ingestion/workflow.json"

if ($LASTEXITCODE -eq 0) {
    Write-Host "Logic App deployed successfully." -ForegroundColor Green
} else {
    Write-Host "Logic App deployment failed!" -ForegroundColor Red
}

# --- 2. Deploy Web App (agent + dashboard code) ---
Write-Host "`n=== Deploying Web App code ===" -ForegroundColor Cyan

# Ensure the Web App is started before deploying
az webapp start --resource-group $resourceGroup --name $webAppName 2>$null

# Zip the application code (exclude non-deployment files)
$zipPath = "$env:TEMP\webapp-deploy.zip"
if (Test-Path $zipPath) { Remove-Item $zipPath }

Compress-Archive -Path @(
    "src",
    "utils",
    "requirements.txt",
    "startup.sh",
    "gunicorn.conf.py",
    "inbox_agent.py",
    "graph_tools.py",
    "graph_tools_new.py"
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

# --- 3. Add STORAGE_ACCOUNT_URL app setting (required by LinkDownloadTool) ---
Write-Host "`n=== Ensuring STORAGE_ACCOUNT_URL app setting ===" -ForegroundColor Cyan

$storageAccount = az resource list -g $resourceGroup `
  --query "[?type=='Microsoft.Storage/storageAccounts'].name" -o tsv

az webapp config appsettings set `
  --resource-group $resourceGroup `
  --name $webAppName `
  --settings "STORAGE_ACCOUNT_URL=https://$storageAccount.blob.core.windows.net"

Write-Host "`n=== Deployment complete ===" -ForegroundColor Green
Write-Host "Logic App: $logicAppName"
Write-Host "Web App:   https://$webAppName.azurewebsites.net"