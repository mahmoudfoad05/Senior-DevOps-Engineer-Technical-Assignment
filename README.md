# Senior-DevOps-Engineer-Technical-Assignment
# Docker Swarm Production Deployment


## Architecture Overview

This project deploys a production-ready microservices stack on Docker Swarm with:

- **Frontend**: Next.js (3 replicas, rolling updates, zero-downtime)
- **Backend API**: Node.js/Express (3 replicas, health checks, sticky sessions)
- **Database**: PostgreSQL 16 (single replica with WAL, pinned to labeled node)
- **Cache**: Redis 7 (session store + cache, AOF persistence)
- **Ingress**: Traefik v3 (SSL termination, path-based routing, rate limiting)
- **Monitoring**: Prometheus + Grafana + Node Exporter + cAdvisor + Alertmanager
- **Secrets**: Docker Swarm native secrets (never environment variables)

### Network Topology

```
Internet
    │
    ▼
[Traefik] ── public network ─────────────────────────────────┐
    │                                                         │
    │ frontend_net                                            │
    ▼                                                         │
[Frontend x3] ──────────────────────────────────────────┐    │
    │                                                    │    │
    │ frontend_net (API calls)                           │    │
    ▼                                                    │    │
[Backend x3] ───────────────────────────────────────┐   │    │
    │                                               │   │    │
    │ backend_net                                   │   │    │
    ├──────────────────┐                            │   │    │
    ▼                  ▼                            │   │    │
[PostgreSQL]       [Redis]                          │   │    │
                                                    │   │    │
[Prometheus] ── monitoring network ─────────────────┘   │    │
[Grafana]    ──────────────────────────────────────────┘    │
[AlertManager] ──────────────────────────────────────────────┘
```

Encrypted overlay networks separate each tier — the database is unreachable from the frontend, and the public network only contains the Traefik ingress.

---

## Quick Start

### Prerequisites

- Docker Engine 24+ with Swarm mode
- 3 nodes (1+ manager, 2+ workers) or [Play With Docker](https://labs.play-with-docker.com/)
- Domain with DNS pointing to your manager IP (or use `nip.io` for local testing)

### 1. Initialize Swarm

```bash
# On the first manager node
docker swarm init --advertise-addr <MANAGER-IP>

# Add worker nodes (token from above command)
docker swarm join --token <WORKER-TOKEN> <MANAGER-IP>:2377

# Label nodes for placement constraints
docker node update --label-add zone=az1 --label-add app=backend worker-1
docker node update --label-add zone=az2 --label-add app=backend worker-2
docker node update --label-add postgres=true worker-1
```

### 2. Create Secrets

```bash
# Auto-generate secure passwords and create all secrets
chmod +x scripts/secrets_setup.sh
./scripts/secrets_setup.sh create

# Or manually:
echo "your-db-password" | docker secret create db_password -
echo "appuser" | docker secret create db_username -
echo "postgresql://appuser:your-db-password@postgres:5432/appdb" | docker secret create db_connection_string -
echo "your-redis-password" | docker secret create redis_password -
echo "your-jwt-secret-min-32-chars" | docker secret create jwt_secret -
echo "your-grafana-password" | docker secret create grafana_admin_password -
```

### 3. Create Configs

```bash
docker config create nginx_config ./configs/nginx.conf
docker config create app_config ./configs/app.json
docker config create prometheus_config ./monitoring/prometheus.yml
docker config create alertmanager_config ./monitoring/alertmanager.yml
```

### 4. Deploy the Stack

```bash
# Set image tags
export FRONTEND_TAG=v1.0.0
export BACKEND_TAG=v1.0.0

# Deploy via Docker CLI
docker stack deploy --with-registry-auth -c docker-compose.yml app

# OR deploy via Portainer API
python3 scripts/portainer_deploy.py deploy \
  --stack app \
  --compose docker-compose.yml \
  --env "FRONTEND_TAG=v1.0.0" \
  --env "BACKEND_TAG=v1.0.0" \
  --wait
```

### 5. Verify Deployment

```bash
# Check all services are running
docker service ls

# Expected output:
# NAME                  MODE        REPLICAS  IMAGE
# app_traefik           global      3/3       traefik:v3.0
# app_frontend          replicated  3/3       frontend:v1.0.0
# app_backend           replicated  3/3       backend:v1.0.0
# app_postgres          replicated  1/1       postgres:16-alpine
# app_redis             replicated  1/1       redis:7-alpine
# app_prometheus        replicated  1/1       prom/prometheus
# app_grafana           replicated  1/1       grafana/grafana
# app_node_exporter     global      3/3       prom/node-exporter
# app_cadvisor          global      3/3       gcr.io/cadvisor/cadvisor

# Run smoke test
curl -sf https://example.com/api/health
```

---


## Common Operations

```bash
# Scale backend replicas
docker service scale app_backend=5

# Rolling update to new image
docker service update --image ghcr.io/yourorg/backend:v2.0.0 app_backend

# Rollback last update
docker service rollback app_backend

# View logs for all backend tasks
docker service logs app_backend --follow --timestamps

# Drain a node for maintenance
docker node update --availability drain <node-id>

# Rotate a secret (zero downtime)
./scripts/secrets_setup.sh rotate db_password "new-secure-password"

# Deploy via Portainer API
python3 scripts/portainer_deploy.py deploy --stack app --compose docker-compose.yml --wait
```

---

## Assumptions

1. **Registry**: GHCR (GitHub Container Registry) is used.
2. **Domain**: Replace `example.com` throughout with your actual domain. For local testing, use `<manager-ip>.nip.io`.
3. **Play With Docker**: On PWD, replace `mode: host` Traefik port publishing with `mode: ingress` since PWD controls port mapping.
4. **Storage**: Named volumes use `driver: local`. For multi-node Swarm, replace with a shared volume driver (NFS, GlusterFS, or a CSI plugin) to ensure database volumes are accessible regardless of which node the task schedules on.
5. **TLS Certificates**: The Traefik Let's Encrypt configuration requires a publicly accessible domain. For internal/air-gapped environments, replace `tlschallenge` with `dnsChallenge` or mount pre-existing certificates.
