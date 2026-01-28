// ============================================================================
// Azure Cosmos DB
// ============================================================================
// NoSQL database for tracking email processing status, metadata, and audit logs
// Using serverless for MVP (cost-effective for variable workloads)
// ============================================================================

@description('Name of the Cosmos DB account')
param name string

@description('Azure region')
param location string

@description('Resource tags')
param tags object

// ============================================================================
// Resources
// ============================================================================

resource cosmosAccount 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' = {
  name: name
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    enableFreeTier: false
    capabilities: [
      {
        name: 'EnableServerless' // Serverless for MVP - pay per request
      }
    ]
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session' // Good balance for this use case
    }
    locations: [
      {
        locationName: location
        failoverPriority: 0
        isZoneRedundant: false
      }
    ]
    publicNetworkAccess: 'Enabled'
    enableAutomaticFailover: false
    enableMultipleWriteLocations: false
  }
}

// Database for email processing
resource database 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-05-15' = {
  parent: cosmosAccount
  name: 'email-processing'
  properties: {
    resource: {
      id: 'email-processing'
    }
  }
}

// ----------------------------------------------------------------------------
// Container: emails
// Main container for email processing records
// ----------------------------------------------------------------------------
resource emailsContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: database
  name: 'emails'
  properties: {
    resource: {
      id: 'emails'
      partitionKey: {
        paths: ['/status'] // Partition by status for efficient queries
        kind: 'Hash'
      }
      indexingPolicy: {
        indexingMode: 'consistent'
        automatic: true
        includedPaths: [
          { path: '/*' }
        ]
        excludedPaths: [
          { path: '/"_etag"/?' }
          { path: '/emailBody/?' } // Exclude large text from indexing
          { path: '/attachmentContent/?' }
        ]
        compositeIndexes: [
          [
            { path: '/status', order: 'ascending' }
            { path: '/receivedAt', order: 'descending' }
          ]
          [
            { path: '/confidenceLevel', order: 'ascending' }
            { path: '/receivedAt', order: 'descending' }
          ]
        ]
      }
      defaultTtl: -1 // No automatic expiration
    }
  }
}

// ----------------------------------------------------------------------------
// Container: classifications
// Store classification results and confidence scores
// ----------------------------------------------------------------------------
resource classificationsContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: database
  name: 'classifications'
  properties: {
    resource: {
      id: 'classifications'
      partitionKey: {
        paths: ['/eventType'] // Partition by event type
        kind: 'Hash'
      }
      indexingPolicy: {
        indexingMode: 'consistent'
        automatic: true
        includedPaths: [
          { path: '/*' }
        ]
        excludedPaths: [
          { path: '/"_etag"/?' }
        ]
      }
    }
  }
}

// ----------------------------------------------------------------------------
// Container: audit-logs
// Audit trail for all processing actions
// ----------------------------------------------------------------------------
resource auditLogsContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: database
  name: 'audit-logs'
  properties: {
    resource: {
      id: 'audit-logs'
      partitionKey: {
        paths: ['/action'] // Partition by action type
        kind: 'Hash'
      }
      defaultTtl: 7776000 // 90 days retention
      indexingPolicy: {
        indexingMode: 'consistent'
        automatic: true
        includedPaths: [
          { path: '/emailId/?' }
          { path: '/action/?' }
          { path: '/timestamp/?' }
          { path: '/userId/?' }
        ]
        excludedPaths: [
          { path: '/*' } // Exclude everything else for cost savings
        ]
      }
    }
  }
}

// ----------------------------------------------------------------------------
// Container: pe-events
// Unique PE events (deduplicated from emails)
// One document per unique capital call, distribution, etc.
// ----------------------------------------------------------------------------
resource peEventsContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: database
  name: 'pe-events'
  properties: {
    resource: {
      id: 'pe-events'
      partitionKey: {
        paths: ['/eventType'] // Partition by event type (Capital Call, Distribution, etc.)
        kind: 'Hash'
      }
      indexingPolicy: {
        indexingMode: 'consistent'
        automatic: true
        includedPaths: [
          { path: '/*' }
        ]
        excludedPaths: [
          { path: '/"_etag"/?' }
        ]
        compositeIndexes: [
          [
            { path: '/eventType', order: 'ascending' }
            { path: '/createdAt', order: 'descending' }
          ]
          [
            { path: '/peCompany', order: 'ascending' }
            { path: '/fundName', order: 'ascending' }
          ]
        ]
      }
      // Unique constraint via dedup_key field
      uniqueKeyPolicy: {
        uniqueKeys: [
          { paths: ['/dedupKey'] }
        ]
      }
    }
  }
}

// ----------------------------------------------------------------------------
// Container: fund-mappings
// Reference data for fund/share class mappings (for fine-grained classification)
// ----------------------------------------------------------------------------
resource fundMappingsContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: database
  name: 'fund-mappings'
  properties: {
    resource: {
      id: 'fund-mappings'
      partitionKey: {
        paths: ['/fundId']
        kind: 'Hash'
      }
    }
  }
}

// ============================================================================
// Outputs
// ============================================================================

@description('Cosmos DB account name')
output name string = cosmosAccount.name

@description('Cosmos DB account ID')
output id string = cosmosAccount.id

@description('Cosmos DB endpoint')
output endpoint string = cosmosAccount.properties.documentEndpoint

@description('Cosmos DB database name')
output databaseName string = database.name

@description('Cosmos DB primary key')
@secure()
output primaryKey string = cosmosAccount.listKeys().primaryMasterKey
