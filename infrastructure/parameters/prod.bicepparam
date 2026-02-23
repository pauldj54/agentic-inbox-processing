// ============================================================================
// Production Environment Parameters
// ============================================================================

using '../main.bicep'

param environment = 'prod'
param baseName = 'zava'
param tags = {
  project: 'zava-pe-automation'
  environment: 'prod'
  managedBy: 'bicep'
  costCenter: 'operations'
}
