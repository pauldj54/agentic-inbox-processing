// ============================================================================
// Web App (Admin Dashboard)
// ============================================================================
// Azure App Service for hosting the admin and business user dashboard
// Configured for Python (to match existing codebase) or Node.js
// ============================================================================

@description('Name of the Web App')
param name string

@description('Azure region')
param location string

@description('Resource tags')
param tags object

@description('App Service Plan ID')
param appServicePlanId string

@description('Log Analytics Workspace ID for diagnostics')
param logAnalyticsWorkspaceId string

@description('Cosmos DB endpoint for data access')
param cosmosDbEndpoint string

@description('Cosmos DB database name')
param cosmosDbDatabaseName string

@description('Storage account name')
param storageAccountName string

@description('Service Bus namespace')
param serviceBusNamespace string

@description('Document Intelligence endpoint')
param documentIntelligenceEndpoint string = ''

// ============================================================================
// Resources
// ============================================================================

resource webApp 'Microsoft.Web/sites@2023-12-01' = {
  name: name
  location: location
  tags: tags
  kind: 'app,linux'
  identity: {
    type: 'SystemAssigned' // For managed identity access to other resources
  }
  properties: {
    serverFarmId: appServicePlanId
    httpsOnly: true
    publicNetworkAccess: 'Enabled'
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.11'
      alwaysOn: false // Can be false for MVP to save costs
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      http20Enabled: true
      appSettings: [
        {
          name: 'COSMOS_DB_ENDPOINT'
          value: cosmosDbEndpoint
        }
        {
          name: 'COSMOS_DB_DATABASE'
          value: cosmosDbDatabaseName
        }
        {
          name: 'STORAGE_ACCOUNT_NAME'
          value: storageAccountName
        }
        {
          name: 'SERVICE_BUS_NAMESPACE'
          value: serviceBusNamespace
        }
        {
          name: 'DOCUMENT_INTELLIGENCE_ENDPOINT'
          value: documentIntelligenceEndpoint
        }
        {
          name: 'SCM_DO_BUILD_DURING_DEPLOYMENT'
          value: 'true'
        }
        {
          name: 'WEBSITE_RUN_FROM_PACKAGE'
          value: '0'
        }
        // Azure AD / Entra ID settings (to be configured)
        {
          name: 'AZURE_TENANT_ID'
          value: '' // Configure after deployment
        }
        {
          name: 'AZURE_CLIENT_ID'
          value: '' // Configure after deployment
        }
      ]
    }
  }
}

// Authentication configuration (Entra ID)
resource authSettings 'Microsoft.Web/sites/config@2023-12-01' = {
  parent: webApp
  name: 'authsettingsV2'
  properties: {
    globalValidation: {
      requireAuthentication: true
      unauthenticatedClientAction: 'RedirectToLoginPage'
    }
    identityProviders: {
      azureActiveDirectory: {
        enabled: true
        registration: {
          openIdIssuer: '${environment().authentication.loginEndpoint}common/v2.0'
          clientId: '' // Configure after App Registration
        }
        validation: {
          allowedAudiences: []
        }
      }
    }
    login: {
      tokenStore: {
        enabled: true
      }
    }
  }
}

// Diagnostic settings for monitoring
resource diagnosticSettings 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'diag-${name}'
  scope: webApp
  properties: {
    workspaceId: logAnalyticsWorkspaceId
    logs: [
      {
        category: 'AppServiceHTTPLogs'
        enabled: true
      }
      {
        category: 'AppServiceConsoleLogs'
        enabled: true
      }
      {
        category: 'AppServiceAppLogs'
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

@description('Web App name')
output name string = webApp.name

@description('Web App URL')
output url string = 'https://${webApp.properties.defaultHostName}'

@description('Web App principal ID (managed identity)')
output principalId string = webApp.identity.principalId

@description('Web App resource ID')
output id string = webApp.id
