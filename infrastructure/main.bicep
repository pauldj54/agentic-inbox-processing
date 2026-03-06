// ============================================================================
// Zava PE Email Automation - Main Infrastructure Template
// ============================================================================
// This template orchestrates the deployment of all Azure resources for the
// email processing automation MVP.
// ============================================================================

targetScope = 'resourceGroup'

// ============================================================================
// Parameters
// ============================================================================

@description('Environment name (dev, staging, prod)')
@allowed(['dev', 'staging', 'prod'])
param environment string = 'dev'

@description('Base name for all resources')
@minLength(3)
@maxLength(11)
param baseName string = 'docproc'

@description('Tags to apply to all resources')
param tags object = {
  project: 'pe-automation'
  environment: environment
  managedBy: 'bicep'
}

// ============================================================================
// Variables
// ============================================================================

var resourceSuffix = '${baseName}-${environment}-${uniqueString(resourceGroup().id)}'
var shortSuffix = take(uniqueString(resourceGroup().id), 8)

// ============================================================================
// Modules
// ============================================================================

// Log Analytics Workspace (shared monitoring)
module logAnalytics 'modules/log-analytics.bicep' = {
  name: 'log-analytics-deployment'
  params: {
    name: 'law-${resourceSuffix}'
    location: resourceGroup().location
    tags: tags
  }
}

// Storage Account (attachments, metadata, Logic App connection)
module storage 'modules/storage-account.bicep' = {
  name: 'storage-deployment'
  params: {
    name: 'st${baseName}${environment}${shortSuffix}'
    location: resourceGroup().location
    tags: tags
  }
}

// Service Bus (message queues for async processing)
module serviceBus 'modules/service-bus.bicep' = {
  name: 'service-bus-deployment'
  params: {
    name: 'sb-${resourceSuffix}'
    location: resourceGroup().location
    tags: tags
  }
}

// Cosmos DB (processing status and metadata)
module cosmosDb 'modules/cosmos-db.bicep' = {
  name: 'cosmos-db-deployment'
  params: {
    name: 'cosmos-${resourceSuffix}'
    location: resourceGroup().location
    tags: tags
  }
}

// Document Intelligence (PDF extraction with Layout model)
module documentIntelligence 'modules/document-intelligence.bicep' = {
  name: 'document-intelligence-deployment'
  params: {
    name: 'docint-${resourceSuffix}'
    location: resourceGroup().location
    tags: tags
    sku: environment == 'prod' ? 'S0' : 'S0' // S0 for both, F0 has limits
  }
}

// App Service Plan for Web App (Linux)
module appServicePlan 'modules/app-service-plan.bicep' = {
  name: 'app-service-plan-deployment'
  params: {
    name: 'asp-${resourceSuffix}'
    location: resourceGroup().location
    tags: tags
    sku: environment == 'prod' ? 'P1v3' : 'B1'
  }
}

// Web App (Admin Dashboard)
module webApp 'modules/web-app.bicep' = {
  name: 'web-app-deployment'
  params: {
    name: 'app-${resourceSuffix}'
    location: resourceGroup().location
    tags: tags
    appServicePlanId: appServicePlan.outputs.id
    logAnalyticsWorkspaceId: logAnalytics.outputs.id
    cosmosDbEndpoint: cosmosDb.outputs.endpoint
    cosmosDbDatabaseName: cosmosDb.outputs.databaseName
    storageAccountName: storage.outputs.name
    serviceBusNamespace: serviceBus.outputs.namespaceName
    documentIntelligenceEndpoint: documentIntelligence.outputs.endpoint
    authClientId: '9a517e48-aa49-4af4-82b0-34c7587841c4'
    authTenantId: tenant().tenantId
  }
}

// Logic App Consumption (serverless email trigger workflow - no storage dependency)
module logicApp 'modules/logic-app.bicep' = {
  name: 'logic-app-deployment'
  params: {
    name: 'logic-${resourceSuffix}'
    location: resourceGroup().location
    tags: tags
    serviceBusNamespace: serviceBus.outputs.namespaceName
    logAnalyticsWorkspaceId: logAnalytics.outputs.id
  }
}

// Role Assignments for Logic App Managed Identity
module roleAssignments 'modules/role-assignments.bicep' = {
  name: 'role-assignments-deployment'
  params: {
    logicAppPrincipalId: logicApp.outputs.principalId
    storageAccountName: storage.outputs.name
    serviceBusNamespaceName: serviceBus.outputs.namespaceName
  }
}

// Role Assignments for Web App Managed Identity (Cosmos DB, Storage, Service Bus)
module webAppRoleAssignments 'modules/webapp-role-assignments.bicep' = {
  name: 'webapp-role-assignments-deployment'
  params: {
    webAppPrincipalId: webApp.outputs.principalId
    cosmosDbAccountName: cosmosDb.outputs.name
    storageAccountName: storage.outputs.name
    serviceBusNamespaceName: serviceBus.outputs.namespaceName
    documentIntelligenceAccountName: documentIntelligence.outputs.name
  }
}

// ============================================================================
// Outputs
// ============================================================================

@description('Resource group name')
output resourceGroupName string = resourceGroup().name

@description('Storage account name')
output storageAccountName string = storage.outputs.name

@description('Service Bus namespace')
output serviceBusNamespace string = serviceBus.outputs.namespaceName

@description('Cosmos DB endpoint')
output cosmosDbEndpoint string = cosmosDb.outputs.endpoint

@description('Web App URL')
output webAppUrl string = webApp.outputs.url

@description('Document Intelligence endpoint')
output documentIntelligenceEndpoint string = documentIntelligence.outputs.endpoint

@description('Logic App name')
output logicAppName string = logicApp.outputs.name
