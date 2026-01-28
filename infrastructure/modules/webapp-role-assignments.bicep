// ============================================================================
// Role Assignments for Web App Managed Identity
// ============================================================================
// Grants Web App managed identity access to Cosmos DB, Storage, and Service Bus
// ============================================================================

@description('Principal ID of the Web App managed identity')
param webAppPrincipalId string

@description('Cosmos DB account name')
param cosmosDbAccountName string

@description('Storage account name')
param storageAccountName string

@description('Service Bus namespace name')
param serviceBusNamespaceName string

@description('Document Intelligence account name')
param documentIntelligenceAccountName string = ''

// ============================================================================
// Role Definition IDs (built-in Azure roles)
// ============================================================================

// Cosmos DB Built-in Data Reader - read data from Cosmos DB
var cosmosDbDataReaderRoleId = '00000000-0000-0000-0000-000000000001'

// Cosmos DB Built-in Data Contributor - read/write data in Cosmos DB
var cosmosDbDataContributorRoleId = '00000000-0000-0000-0000-000000000002'

// Storage Blob Data Reader - read blob data
var storageBlobDataReaderRoleId = '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'

// Azure Service Bus Data Receiver - receive messages from queues
var serviceBusDataReceiverRoleId = '4f6d3b9b-027b-4f4c-9142-0e5a2a2247e0'

// Cognitive Services User - allows calling Document Intelligence APIs
var cognitiveServicesUserRoleId = 'a97b65f3-24c7-4388-baec-2e87135dc908'

// ============================================================================
// References to existing resources
// ============================================================================

resource cosmosDbAccount 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' existing = {
  name: cosmosDbAccountName
}

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}

resource serviceBusNamespace 'Microsoft.ServiceBus/namespaces@2022-10-01-preview' existing = {
  name: serviceBusNamespaceName
}

resource documentIntelligenceAccount 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = if (!empty(documentIntelligenceAccountName)) {
  name: documentIntelligenceAccountName
}

// ============================================================================
// Cosmos DB Role Assignment (uses SQL RBAC, not Azure RBAC)
// ============================================================================

resource cosmosDbRoleAssignment 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = {
  parent: cosmosDbAccount
  name: guid(cosmosDbAccount.id, webAppPrincipalId, cosmosDbDataContributorRoleId)
  properties: {
    principalId: webAppPrincipalId
    roleDefinitionId: '${cosmosDbAccount.id}/sqlRoleDefinitions/${cosmosDbDataContributorRoleId}'
    scope: cosmosDbAccount.id
  }
}

// ============================================================================
// Storage Role Assignment
// ============================================================================

resource storageBlobDataReaderAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, webAppPrincipalId, storageBlobDataReaderRoleId)
  scope: storageAccount
  properties: {
    principalId: webAppPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataReaderRoleId)
    principalType: 'ServicePrincipal'
  }
}

// ============================================================================
// Service Bus Role Assignment
// ============================================================================

resource serviceBusDataReceiverAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(serviceBusNamespace.id, webAppPrincipalId, serviceBusDataReceiverRoleId)
  scope: serviceBusNamespace
  properties: {
    principalId: webAppPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', serviceBusDataReceiverRoleId)
    principalType: 'ServicePrincipal'
  }
}

// ============================================================================
// Document Intelligence Role Assignment
// ============================================================================

resource documentIntelligenceUserAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(documentIntelligenceAccountName)) {
  name: guid(documentIntelligenceAccount.id, webAppPrincipalId, cognitiveServicesUserRoleId)
  scope: documentIntelligenceAccount
  properties: {
    principalId: webAppPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesUserRoleId)
    principalType: 'ServicePrincipal'
  }
}
