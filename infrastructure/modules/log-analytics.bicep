// ============================================================================
// Log Analytics Workspace
// ============================================================================
// Centralized logging and monitoring for all resources
// ============================================================================

@description('Name of the Log Analytics workspace')
param name string

@description('Azure region')
param location string

@description('Resource tags')
param tags object

@description('Retention period in days')
param retentionInDays int = 30

// ============================================================================
// Resources
// ============================================================================

resource logAnalyticsWorkspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: retentionInDays
    features: {
      enableLogAccessUsingOnlyResourcePermissions: true
    }
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

// ============================================================================
// Outputs
// ============================================================================

@description('Log Analytics workspace ID')
output id string = logAnalyticsWorkspace.id

@description('Log Analytics workspace name')
output name string = logAnalyticsWorkspace.name

@description('Log Analytics workspace customer ID')
output customerId string = logAnalyticsWorkspace.properties.customerId
