// ============================================================================
// SFTP File Ingestion Logic App Consumption (Serverless Workflow)
// ============================================================================
// Monitors SFTP server for new files and ingests them for processing
// Workflow and API connections configured via Azure Portal
// Consumption tier = serverless, no storage dependency, pay-per-execution
// ============================================================================

@description('Name of the SFTP Logic App')
param name string

@description('Azure region')
param location string

@description('Resource tags')
param tags object

@description('Log Analytics Workspace ID')
param logAnalyticsWorkspaceId string

// ============================================================================
// Resources
// ============================================================================

resource sftpLogicApp 'Microsoft.Logic/workflows@2019-05-01' = {
  name: name
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    state: 'Enabled'
    definition: {
      '$schema': 'https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#'
      contentVersion: '1.0.0.0'
      parameters: {
        '$connections': {
          defaultValue: {}
          type: 'Object'
        }
      }
      triggers: {
        // Placeholder trigger - will be configured via Azure Portal
        manual: {
          type: 'Request'
          kind: 'Http'
          inputs: {
            schema: {}
          }
        }
      }
      actions: {
        Initialize_variable: {
          type: 'InitializeVariable'
          inputs: {
            variables: [
              {
                name: 'sftpProcessed'
                type: 'boolean'
                value: false
              }
            ]
          }
          runAfter: {}
        }
      }
      outputs: {}
    }
    parameters: {}
  }
}

// Diagnostic settings
resource diagnosticSettings 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'diag-${name}'
  scope: sftpLogicApp
  properties: {
    workspaceId: logAnalyticsWorkspaceId
    logs: [
      {
        category: 'WorkflowRuntime'
        enabled: true
      }
    ]
    metrics: [
      {
        category: 'AllMetrics'
        enabled: true
      }
    ]
  }
}

// ============================================================================
// Outputs
// ============================================================================

@description('SFTP Logic App name')
output name string = sftpLogicApp.name

@description('SFTP Logic App resource ID')
output id string = sftpLogicApp.id

@description('SFTP Logic App principal ID (managed identity)')
output principalId string = sftpLogicApp.identity.principalId
