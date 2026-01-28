// ============================================================================
// Azure Service Bus
// ============================================================================
// Message queues for async email processing with dead-letter support
// Provides better message patterns than Storage Queues for this scenario
// ============================================================================

@description('Name of the Service Bus namespace')
param name string

@description('Azure region')
param location string

@description('Resource tags')
param tags object

// ============================================================================
// Resources
// ============================================================================

resource serviceBusNamespace 'Microsoft.ServiceBus/namespaces@2022-10-01-preview' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: 'Standard' // Standard tier for topics, sessions, and more queues
    tier: 'Standard'
  }
  properties: {
    minimumTlsVersion: '1.2'
    publicNetworkAccess: 'Enabled'
  }
}

// ----------------------------------------------------------------------------
// Queue: email-intake
// First stop for all incoming emails from Logic App
// ----------------------------------------------------------------------------
resource emailIntakeQueue 'Microsoft.ServiceBus/namespaces/queues@2022-10-01-preview' = {
  parent: serviceBusNamespace
  name: 'email-intake'
  properties: {
    maxDeliveryCount: 5
    defaultMessageTimeToLive: 'P7D' // 7 days
    lockDuration: 'PT5M' // 5 minutes lock for processing
    deadLetteringOnMessageExpiration: true
    requiresSession: false
    enablePartitioning: false
  }
}

// ----------------------------------------------------------------------------
// Queue: classification-pending
// Emails waiting for AI classification
// ----------------------------------------------------------------------------
resource classificationQueue 'Microsoft.ServiceBus/namespaces/queues@2022-10-01-preview' = {
  parent: serviceBusNamespace
  name: 'classification-pending'
  properties: {
    maxDeliveryCount: 3
    defaultMessageTimeToLive: 'P7D'
    lockDuration: 'PT2M' // 2 minutes - classification should be quick
    deadLetteringOnMessageExpiration: true
  }
}

// ----------------------------------------------------------------------------
// Queue: human-review
// Emails with confidence < 80% requiring human intervention
// Note: Lock duration max is 5 min. Human review handled via app logic, not lock.
// ----------------------------------------------------------------------------
resource humanReviewQueue 'Microsoft.ServiceBus/namespaces/queues@2022-10-01-preview' = {
  parent: serviceBusNamespace
  name: 'human-review'
  properties: {
    maxDeliveryCount: 10
    defaultMessageTimeToLive: 'P14D' // 14 days - more time for human review
    lockDuration: 'PT5M' // 5 minutes max - human review state tracked in Cosmos DB
    deadLetteringOnMessageExpiration: true
  }
}

// ----------------------------------------------------------------------------
// Queue: archival-pending
// Documents ready to be archived to SharePoint
// ----------------------------------------------------------------------------
resource archivalQueue 'Microsoft.ServiceBus/namespaces/queues@2022-10-01-preview' = {
  parent: serviceBusNamespace
  name: 'archival-pending'
  properties: {
    maxDeliveryCount: 5
    defaultMessageTimeToLive: 'P7D'
    lockDuration: 'PT5M'
    deadLetteringOnMessageExpiration: true
  }
}

// ----------------------------------------------------------------------------
// Queue: processing-complete
// Successfully processed emails for notification/logging
// ----------------------------------------------------------------------------
resource completedQueue 'Microsoft.ServiceBus/namespaces/queues@2022-10-01-preview' = {
  parent: serviceBusNamespace
  name: 'processing-complete'
  properties: {
    maxDeliveryCount: 3
    defaultMessageTimeToLive: 'P1D' // 1 day - just for notifications
    lockDuration: 'PT1M'
    deadLetteringOnMessageExpiration: false // No need to keep completed notifications
  }
}

// ----------------------------------------------------------------------------
// Queue: discarded
// Non-PE emails discarded by classification but available for manual review
// ----------------------------------------------------------------------------
resource discardedQueue 'Microsoft.ServiceBus/namespaces/queues@2022-10-01-preview' = {
  parent: serviceBusNamespace
  name: 'discarded'
  properties: {
    maxDeliveryCount: 3
    defaultMessageTimeToLive: 'P30D' // 30 days - keep for a while in case of review
    lockDuration: 'PT5M'
    deadLetteringOnMessageExpiration: false
  }
}

// Authorization rule for application access
resource sendListenRule 'Microsoft.ServiceBus/namespaces/AuthorizationRules@2022-10-01-preview' = {
  parent: serviceBusNamespace
  name: 'app-access'
  properties: {
    rights: [
      'Send'
      'Listen'
      'Manage'
    ]
  }
}

// ============================================================================
// Outputs
// ============================================================================

@description('Service Bus namespace name')
output namespaceName string = serviceBusNamespace.name

@description('Service Bus namespace ID')
output id string = serviceBusNamespace.id

@description('Service Bus connection string')
@secure()
output connectionString string = sendListenRule.listKeys().primaryConnectionString

@description('Email intake queue name')
output emailIntakeQueueName string = emailIntakeQueue.name

@description('Classification queue name')
output classificationQueueName string = classificationQueue.name

@description('Human review queue name')
output humanReviewQueueName string = humanReviewQueue.name

@description('Archival queue name')
output archivalQueueName string = archivalQueue.name

@description('Discarded queue name')
output discardedQueueName string = discardedQueue.name
