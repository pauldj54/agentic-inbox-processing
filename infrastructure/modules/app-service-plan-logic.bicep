// ============================================================================
// App Service Plan for Logic App (Windows)
// ============================================================================
// Logic App Standard requires Windows App Service Plan with Workflow Standard SKU
// ============================================================================

@description('Name of the App Service Plan')
param name string

@description('Azure region')
param location string

@description('Resource tags')
param tags object

@description('SKU name for Logic App (WS1 for dev, WS2 for prod)')
@allowed(['WS1', 'WS2', 'WS3'])
param sku string = 'WS1'

// ============================================================================
// Resources
// ============================================================================

resource appServicePlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: name
  location: location
  tags: tags
  kind: '' // Empty for Windows
  sku: {
    name: sku
    tier: 'WorkflowStandard'
    capacity: 1
  }
  properties: {
    reserved: false // Windows
    zoneRedundant: false
  }
}

// ============================================================================
// Outputs
// ============================================================================

@description('App Service Plan ID')
output id string = appServicePlan.id

@description('App Service Plan name')
output name string = appServicePlan.name
