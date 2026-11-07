#!/usr/bin/env bash
# Builds the whole lab rig in one resource group. Run from the repo root in Azure Cloud Shell (Bash).
# Takes 15-25 minutes. Writes the URLs and connection strings to .env.lab (keep it out of git).
set -euo pipefail
RG=${RG:-rg-sre-lab}
LOC=${LOC:-westeurope}
SUFFIX=${SUFFIX:-$(openssl rand -hex 3)}
PG=pg-sre-$SUFFIX
ENV=cae-sre
PGPASS=$(openssl rand -hex 16)

az extension add --name containerapp --upgrade -o none
az provider register --namespace Microsoft.App -o none
az provider register --namespace Microsoft.OperationalInsights -o none

az group create -n $RG -l $LOC -o none
az monitor log-analytics workspace create -g $RG -n law-sre -l $LOC -o none
LAW_ID=$(az monitor log-analytics workspace show -g $RG -n law-sre --query customerId -o tsv)
LAW_KEY=$(az monitor log-analytics workspace get-shared-keys -g $RG -n law-sre --query primarySharedKey -o tsv)
LAW_RES=$(az monitor log-analytics workspace show -g $RG -n law-sre --query id -o tsv)
az monitor app-insights component create -a appi-sre -g $RG -l $LOC --workspace "$LAW_RES" -o none
AI_CONN=$(az monitor app-insights component show -a appi-sre -g $RG --query connectionString -o tsv)

az postgres flexible-server create -g $RG -n $PG -l $LOC --tier Burstable --sku-name Standard_B1ms \
  --storage-size 32 --version 16 --admin-user boxadmin --admin-password "$PGPASS" \
  --public-access 0.0.0.0 --yes -o none
az postgres flexible-server db create -g $RG -s $PG -d boxoffice -o none
DBURL="postgresql://boxadmin:$PGPASS@$PG.postgres.database.azure.com:5432/boxoffice?sslmode=require"
psql "$DBURL" -f rig/schema.sql

az containerapp env create -n $ENV -g $RG -l $LOC --logs-workspace-id "$LAW_ID" --logs-workspace-key "$LAW_KEY" -o none

az containerapp up -n deps -g $RG --environment $ENV --source deps --ingress external --target-port 8000 \
  --env-vars OTEL_SERVICE_NAME=deps APPLICATIONINSIGHTS_CONNECTION_STRING="$AI_CONN"
az containerapp update -n deps -g $RG --min-replicas 1 --max-replicas 1 -o none
DEPS=https://$(az containerapp show -n deps -g $RG --query properties.configuration.ingress.fqdn -o tsv)

az containerapp up -n boxoffice -g $RG --environment $ENV --source boxoffice --ingress external --target-port 8000 \
  --env-vars RELEASE=v1 DATABASE_URL="$DBURL" DEPS_URL="$DEPS" OTEL_SERVICE_NAME=boxoffice \
  APPLICATIONINSIGHTS_CONNECTION_STRING="$AI_CONN"
az containerapp update -n boxoffice -g $RG --min-replicas 1 --max-replicas 1 -o none
BOX=https://$(az containerapp show -n boxoffice -g $RG --query properties.configuration.ingress.fqdn -o tsv)

az containerapp up -n loadgen -g $RG --environment $ENV --source load \
  --env-vars TARGET="$BOX" SHAPE=trickle CLIENT_RETRIES=3
az containerapp update -n loadgen -g $RG --min-replicas 1 --max-replicas 1 --cpu 1 --memory 2Gi -o none

cat > .env.lab <<ENV
RG=$RG
LOC=$LOC
PG=$PG
BOX=$BOX
DEPS=$DEPS
DBURL='$DBURL'
AI_CONN='$AI_CONN'
ENV
echo "Rig is up. Boxoffice: $BOX   Faults: $DEPS/admin/faults   Settings saved to .env.lab"
