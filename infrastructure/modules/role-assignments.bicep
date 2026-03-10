// ============================================================================
// Role Assignments for Managed Identity Access
// ============================================================================
// Grants Logic App managed identity access to Storage and Service Bus
// Required when shared key access is disabled
// ============================================================================

@description('Principal ID of the Logic App managed identity')
param logicAppPrincipalId string

@description('Principal ID of the SFTP Logic App managed identity')
param sftpLogicAppPrincipalId string

@description('Storage account name')
param storageAccountName string

@description('Service Bus namespace name')
param serviceBusNamespaceName string

@description('Cosmos DB account name')
param cosmosDbAccountName string

// ============================================================================
// Role Definition IDs (built-in Azure roles)
// ============================================================================

// Storage Blob Data Owner - full access to blob data
var storageBlobDataOwnerRoleId = 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b'

// Storage Account Contributor - manage storage account
var storageAccountContributorRoleId = '17d1049b-9a84-46fb-8f53-869881c3d3ab'

// Storage Queue Data Contributor - read/write queue data
var storageQueueDataContributorRoleId = '974c5e8b-45b9-4653-ba55-5f855dd0fb88'

// Storage File Data SMB Share Contributor - for file shares (content share)
var storageFileDataContributorRoleId = '0c867c2a-1d8c-454a-a3db-ab2ea1bdc8bb'

// Azure Service Bus Data Owner - full access to Service Bus
var serviceBusDataOwnerRoleId = '090c5cfd-751d-490a-894a-3ce6f1109419'

// Cosmos DB Built-in Data Contributor - read/write Cosmos DB data
var cosmosDbDataContributorRoleId = '00000000-0000-0000-0000-000000000002'

// ============================================================================
// References to existing resources
// ============================================================================

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}

resource serviceBusNamespace 'Microsoft.ServiceBus/namespaces@2022-10-01-preview' existing = {
  name: serviceBusNamespaceName
}

resource cosmosDbAccount 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' existing = {
  name: cosmosDbAccountName
}

// ============================================================================
// Storage Role Assignments
// ============================================================================

resource storageBlobDataOwnerAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, logicAppPrincipalId, storageBlobDataOwnerRoleId)
  scope: storageAccount
  properties: {
    principalId: logicAppPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataOwnerRoleId)
    principalType: 'ServicePrincipal'
  }
}

resource storageAccountContributorAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, logicAppPrincipalId, storageAccountContributorRoleId)
  scope: storageAccount
  properties: {
    principalId: logicAppPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageAccountContributorRoleId)
    principalType: 'ServicePrincipal'
  }
}

resource storageQueueDataContributorAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, logicAppPrincipalId, storageQueueDataContributorRoleId)
  scope: storageAccount
  properties: {
    principalId: logicAppPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageQueueDataContributorRoleId)
    principalType: 'ServicePrincipal'
  }
}

resource storageFileDataContributorAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, logicAppPrincipalId, storageFileDataContributorRoleId)
  scope: storageAccount
  properties: {
    principalId: logicAppPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageFileDataContributorRoleId)
    principalType: 'ServicePrincipal'
  }
}

// ============================================================================
// Service Bus Role Assignment
// ============================================================================

resource serviceBusDataOwnerAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(serviceBusNamespace.id, logicAppPrincipalId, serviceBusDataOwnerRoleId)
  scope: serviceBusNamespace
  properties: {
    principalId: logicAppPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', serviceBusDataOwnerRoleId)
    principalType: 'ServicePrincipal'
  }
}

// ============================================================================
// SFTP Logic App Role Assignments
// ============================================================================

// Storage Blob Data Contributor for SFTP Logic App
resource sftpStorageBlobAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, sftpLogicAppPrincipalId, storageBlobDataOwnerRoleId)
  scope: storageAccount
  properties: {
    principalId: sftpLogicAppPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataOwnerRoleId)
    principalType: 'ServicePrincipal'
  }
}

// Service Bus Data Sender for SFTP Logic App
resource sftpServiceBusAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(serviceBusNamespace.id, sftpLogicAppPrincipalId, serviceBusDataOwnerRoleId)
  scope: serviceBusNamespace
  properties: {
    principalId: sftpLogicAppPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', serviceBusDataOwnerRoleId)
    principalType: 'ServicePrincipal'
  }
}

// Cosmos DB SQL Role Assignment for SFTP Logic App (data plane access)
resource sftpCosmosDbSqlRoleAssignment 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = {
  parent: cosmosDbAccount
  name: guid(cosmosDbAccount.id, sftpLogicAppPrincipalId, 'cosmos-sql-contributor')
  properties: {
    principalId: sftpLogicAppPrincipalId
    roleDefinitionId: '${cosmosDbAccount.id}/sqlRoleDefinitions/${cosmosDbDataContributorRoleId}'
    scope: cosmosDbAccount.id
  }
}
