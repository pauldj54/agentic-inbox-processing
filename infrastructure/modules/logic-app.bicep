// ============================================================================
// Logic App Consumption (Serverless Workflow)
// ============================================================================
// Event-driven workflow triggered by new emails in Microsoft 365 inbox
// Extracts attachments and sends messages to Service Bus for processing
// Consumption tier = serverless, no storage dependency, pay-per-execution
// ============================================================================

@description('Name of the Logic App')
param name string

@description('Azure region')
param location string

@description('Resource tags')
param tags object

@description('Service Bus namespace name')
param serviceBusNamespace string

@description('Log Analytics Workspace ID')
param logAnalyticsWorkspaceId string

// ============================================================================
// Resources
// ============================================================================

resource logicApp 'Microsoft.Logic/workflows@2019-05-01' = {
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
        // Placeholder action - workflow will be designed in Portal
        Initialize_variable: {
          type: 'InitializeVariable'
          inputs: {
            variables: [
              {
                name: 'emailProcessed'
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
  scope: logicApp
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

@description('Logic App name')
output name string = logicApp.name

@description('Logic App resource ID')
output id string = logicApp.id

@description('Logic App principal ID (managed identity)')
output principalId string = logicApp.identity.principalId

@description('Logic App trigger URL (for HTTP trigger)')
output triggerUrl string = listCallbackUrl('${logicApp.id}/triggers/manual', '2019-05-01').value
