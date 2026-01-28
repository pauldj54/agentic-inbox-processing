// Azure Document Intelligence (Form Recognizer) resource
// Uses managed identity for authentication

@description('Name of the Document Intelligence resource')
param name string

@description('Location for the resource')
param location string = resourceGroup().location

@description('SKU for Document Intelligence')
@allowed(['F0', 'S0'])
param sku string = 'S0'

@description('Tags for the resource')
param tags object = {}

@description('Principal IDs to grant Cognitive Services User role')
param readerPrincipalIds array = []

resource documentIntelligence 'Microsoft.CognitiveServices/accounts@2023-10-01-preview' = {
  name: name
  location: location
  kind: 'FormRecognizer'
  tags: tags
  sku: {
    name: sku
  }
  properties: {
    customSubDomainName: name
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
    }
    disableLocalAuth: true // Enforce managed identity only
  }
  identity: {
    type: 'SystemAssigned'
  }
}

// Role assignment for Cognitive Services User (allows calling the API)
resource cognitiveServicesUserRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for principalId in readerPrincipalIds: {
  name: guid(documentIntelligence.id, principalId, 'CognitiveServicesUser')
  scope: documentIntelligence
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'a97b65f3-24c7-4388-baec-2e87135dc908') // Cognitive Services User
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}]

@description('Endpoint URL for Document Intelligence')
output endpoint string = documentIntelligence.properties.endpoint

@description('Resource ID')
output resourceId string = documentIntelligence.id

@description('Name of the resource')
output name string = documentIntelligence.name

@description('Principal ID of the managed identity')
output principalId string = documentIntelligence.identity.principalId
