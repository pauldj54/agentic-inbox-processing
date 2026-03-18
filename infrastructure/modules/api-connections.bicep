// ============================================================================
// API Connections for SFTP Logic App (Consumption)
// ============================================================================
// Microsoft.Web/connections resources used by the SFTP file ingestion
// Logic App. These are managed connector instances that the Logic App
// references via its $connections parameter.
//
// Note: Logic Apps Consumption does NOT support managed identity for
// Service Bus API connections. Using SAS key (connection string) auth.
// ============================================================================

@description('Azure region (must match Logic App location)')
param location string

@description('Resource tags')
param tags object

@description('Service Bus namespace name (without .servicebus.windows.net)')
param serviceBusNamespaceName string

@description('Principal ID of the SFTP Logic App managed identity')
param logicAppPrincipalId string

@description('Name of the SFTP Logic App (used for access policy naming)')
param logicAppName string

// Reference the existing Service Bus namespace (in the same resource group)
resource serviceBusNamespace 'Microsoft.ServiceBus/namespaces@2022-10-01-preview' existing = {
  name: serviceBusNamespaceName
}

// Reference the default SAS authorization rule
resource serviceBusAuthRule 'Microsoft.ServiceBus/namespaces/authorizationRules@2022-10-01-preview' existing = {
  parent: serviceBusNamespace
  name: 'RootManageSharedAccessKey'
}

// ============================================================================
// Service Bus API Connection (Connection String / SAS Key)
// ============================================================================

resource serviceBusConnection 'Microsoft.Web/connections@2016-06-01' = {
  name: 'conn-servicebus-sftp'
  location: location
  tags: tags
  properties: {
    displayName: 'Service Bus (SFTP Logic App)'
    api: {
      id: subscriptionResourceId('Microsoft.Web/locations/managedApis', location, 'servicebus')
    }
    parameterValues: {
      connectionString: listKeys(serviceBusAuthRule.id, '2022-10-01-preview').primaryConnectionString
    }
  }
}

// Note: Access policies are only needed for V2 (managed identity) connections.
// With SAS key auth (V1), the connection carries the credential directly.

// ============================================================================
// Outputs
// ============================================================================

@description('Service Bus connection resource ID')
output serviceBusConnectionId string = serviceBusConnection.id

@description('Service Bus connection name')
output serviceBusConnectionName string = serviceBusConnection.name

@description('Service Bus managed API ID')
output serviceBusManagedApiId string = subscriptionResourceId('Microsoft.Web/locations/managedApis', location, 'servicebus')
