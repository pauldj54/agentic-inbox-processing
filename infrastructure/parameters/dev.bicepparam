// ============================================================================
// Development Environment Parameters
// ============================================================================

using '../main.bicep'

param environment = 'dev'
param baseName = 'docproc'
param tags = {
  project: 'pe-automation'
  environment: 'dev'
  managedBy: 'bicep'
  costCenter: 'development'
}
