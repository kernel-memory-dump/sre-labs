# sre-labs-rig

The lab rig for **SRE Field Labs**. Boxoffice is a small ticket-selling API built to
fail in instructive ways, and every lab breaks it on purpose. The manual walks through
each lab; this repository holds the code and scripts the labs run.

**The manual:** [online](https://claude.ai/artifact/Mk24Ku5T4ofRYkVUyse57w), or open
[`docs/sre-field-labs.html`](docs/sre-field-labs.html) in a browser after cloning. It ends
with the Week 7 final assignment, Gatehouse.

The manual's commands say `cd sre-labs-rig`; when you clone this repository the folder is
called `sre-labs`, so use that instead.

## What's here

| Path | What it is |
| --- | --- |
| `boxoffice/` | The service under test (FastAPI, PostgreSQL, OpenTelemetry). Behaviour is controlled by environment variables ("knobs"). |
| `deps/` | Stand-ins for the services Boxoffice depends on: payments, pricing and recommendations. Faults change at runtime through `/admin/faults`. |
| `load/` | Locust traffic shapes (`trickle`, `off`, `steady`, `ramp`, `onsale`, `brownout`). |
| `rig/up.sh` | Builds everything in one resource group (15–25 minutes). |
| `rig/down.sh` | Deletes everything. |
| `rig/schema.sql` | Database schema and seed events. |
| `rig/canary.sh` | Progressive rollout with automatic rollback (Lab 08). |
| `iac/main.bicep` | Bicep starter for Lab 09. Finishing it is part of the lab. |
| `docs/sre-field-labs.html` | The SRE Field Labs manual, labs 01–15 and the final assignment. |

## Quick start (Azure Cloud Shell, Bash)

```bash
git clone https://github.com/kernel-memory-dump/sre-labs.git && cd sre-labs
LOC=westeurope bash rig/up.sh
source .env.lab
curl -s $BOX/events | jq '.events[0]'
```

`up.sh` writes URLs and connection strings to `.env.lab`. That file contains the database
password and is ignored by git. Never commit it.

## Pausing and tearing down

```bash
az postgres flexible-server stop -g $RG -n $PG
az containerapp update -n loadgen -g $RG --set-env-vars SHAPE=off -o none
bash rig/down.sh
```

Set a budget alert on your subscription before running anything. The whole course costs
about $150 if you stop things when idle and tear them down at the end.

## Safety

This is a lab, not production. The `deps` admin endpoint is public and unauthenticated,
and the database accepts connections from any Azure IP. Use fake data only. Lab 09 fixes
part of this as code.
