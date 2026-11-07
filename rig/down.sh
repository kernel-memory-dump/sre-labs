#!/usr/bin/env bash
# Deletes everything the rig created. Billing stops when the deletion finishes.
set -euo pipefail
source .env.lab
az group delete -n "$RG" --yes --no-wait
echo "Deleting $RG in the background. Check with: az group show -n $RG"
