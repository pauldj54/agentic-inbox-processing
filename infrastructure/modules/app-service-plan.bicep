// ============================================================================
// App Service Plan
// ============================================================================
// Shared compute for Web App (Admin Dashboard) and Logic App Standard
// ============================================================================

@description('Name of the App Service Plan')
param name string

@description('Azure region')
param location string

@description('Resource tags')
param tags object

@description('SKU name (B1 for dev, P1v3 for prod)')
@allowed(['B1', 'B2', 'P1v3', 'P2v3'])
param sku string = 'B1'

// ============================================================================
// Resources
// ============================================================================

resource appServicePlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: name
  location: location
  tags: tags
  kind: 'linux'
  sku: {
    name: sku
    capacity: 1
  }
  properties: {
    reserved: true // Required for Linux
    zoneRedundant: false // Not needed for MVP
  }
}

// ============================================================================
// Outputs
// ============================================================================

@description('App Service Plan ID')
output id string = appServicePlan.id

@description('App Service Plan name')
output name string = appServicePlan.name
