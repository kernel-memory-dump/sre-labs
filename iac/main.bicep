// Starter for Lab 09: monitoring, the Container Apps environment and Boxoffice.
// Your job: PostgreSQL, deps, loadgen, the registry role assignment, the action group
// and the burn-rate alert rules from Lab 03. Look up current API versions in the
// Azure resource reference rather than copying old ones.
targetScope = 'resourceGroup'

param location string = resourceGroup().location
param suffix string = uniqueString(resourceGroup().id)
param registryServer string // e.g. myregistry.azurecr.io
param boxofficeImage string // e.g. myregistry.azurecr.io/boxoffice:v1
param depsUrl string
@secure()
param databaseUrl string
param minReplicas int = 2
param maxReplicas int = 6
// Every knob you settled on in Labs 01-08 belongs here, reviewed like code.
param knobs object = {
  RELEASE: 'v1'
  LEGACY_200_ERRORS: '0'
  RESERVE_MODE: 'v2'
  PAY_TIMEOUT_S: '1'
  PAY_ATTEMPTS: '2'
  PAY_BACKOFF: 'jitter'
  PAY_IDEMPOTENCY: '1'
  RECS_TIMEOUT_S: '0.3'
  RECS_BULKHEAD: '8'
  CACHE_EVENTS_S: '2'
  SHED: '1'
  MAX_INFLIGHT: '24'
}

resource law 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: 'law-sre-${suffix}'
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource appi 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-sre-${suffix}'
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: law.id
  }
}

resource cae 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-sre-${suffix}'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: law.properties.customerId
        sharedKey: law.listKeys().primarySharedKey
      }
    }
  }
}

// The app pulls its image with this identity. TODO: give it AcrPull on your registry,
// and make sure that role assignment exists before the app is created.
resource pullIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-pull-${suffix}'
  location: location
}

resource boxoffice 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'boxoffice'
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${pullIdentity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: cae.id
    configuration: {
      activeRevisionsMode: 'Multiple'
      ingress: {
        external: true
        targetPort: 8000
      }
      registries: [
        {
          server: registryServer
          identity: pullIdentity.id
        }
      ]
      secrets: [
        {
          name: 'database-url'
          value: databaseUrl
        }
        {
          name: 'appi-connection'
          value: appi.properties.ConnectionString
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'boxoffice'
          image: boxofficeImage
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: concat([
            {
              name: 'DATABASE_URL'
              secretRef: 'database-url'
            }
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              secretRef: 'appi-connection'
            }
            {
              name: 'DEPS_URL'
              value: depsUrl
            }
            {
              name: 'OTEL_SERVICE_NAME'
              value: 'boxoffice'
            }
          ], map(items(knobs), k => {
            name: k.key
            value: k.value
          }))
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/healthz'
                port: 8000
              }
              periodSeconds: 10
            }
          ]
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
        rules: [
          {
            name: 'http'
            http: {
              metadata: {
                concurrentRequests: '30'
              }
            }
          }
        ]
      }
    }
  }
}

output boxofficeUrl string = 'https://${boxoffice.properties.configuration.ingress.fqdn}'
output appInsightsName string = appi.name
