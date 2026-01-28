// ============================================================================
// Production Environment Parameters
// ============================================================================

using '../main.bicep'

param environment = 'prod'
param baseName = 'quintet'
param tags = {
  project: 'quintet-pe-automation'
  environment: 'prod'
  managedBy: 'bicep'
  costCenter: 'operations'
}
