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

// Key Vault (pre-provisioned, holds sftp-private-key and sharepoint-client-secret)
param keyVaultName = 'kv-docproc-dev-izr2ch55'

// SFTP Logic App now in swedencentral (same region as all other resources)
// Previously in uksouth but moved to swedencentral for managed identity support
