# Docker Swarm Operations & Troubleshooting Runbook

## Part 5.1 — Rolling Updates & Rollbacks

### Zero-Downtime Rolling Update

```bash
# Update a service image with rolling strategy
docker service update \
  --image ghcr.io/yourorg/backend:v2.1.0 \
  --update-parallelism 2 \
  --update-delay 10s \
  --update-failure-action rollback \
  --update-monitor 60s \
  --update-max-failure-ratio 0.2 \
  --update-order start-first \
  app_backend

# Monitor the update in real-time
watch -n 2 'docker service ps app_backend --format "table {{.Name}}\t{{.Image}}\t{{.CurrentState}}\t{{.Error}}"'
```

### Key Update Parameters Explained

| Parameter | Description | Recommended Value |
|-----------|-------------|-------------------|
| `--update-parallelism` | How many tasks to update simultaneously | 1–2 (lower = safer) |
| `--update-delay` | Wait time between updating each batch | 10–30s (allow health checks) |
| `--update-failure-action` | What to do if a task fails during update | `rollback` for prod |
| `--update-monitor` | How long to watch a new task before declaring success | 30–120s |
| `--update-order` | `start-first` (new before old) or `stop-first` | `start-first` for HA |

**Critical distinction:**
- `start-first`: new task starts, becomes healthy, *then* old task stops → requires extra capacity (N+1 replicas needed briefly), ensures zero downtime
- `stop-first`: old task stops first, then new starts → zero extra capacity but brief reduction in running replicas

### Simulating a Failed Deployment

```bash
# Deploy a deliberately broken image to trigger auto-rollback
docker service update \
  --image ghcr.io/yourorg/backend:intentionally-broken \
  --update-failure-action rollback \
  app_backend

# Observe Swarm detect failure and roll back automatically
docker service ps app_backend

# Expected output shows tasks going Rejected/Failed then rollback:
# app_backend.1  backend:broken  Running  Rejected 2m ago  "container exited with code 1"
# app_backend.1  backend:v1.0.0  Running  Running  1m ago
```

### Manual Rollback

```bash
# Rollback ALL tasks to the previous image/config immediately
docker service rollback app_backend

# Rollback with controlled parallelism
docker service update \
  --rollback-parallelism 1 \
  --rollback-delay 5s \
  app_backend

# Inspect rollback state
docker service inspect app_backend --pretty | grep -A5 "RollbackConfig"
```

---

## Part 5.2 — Service Failure Scenarios

### Scenario 1: Services show 3/3 replicas but requests intermittently fail

This is a classic "zombie replica" problem — tasks are running but unhealthy.

```bash
# Step 1: Check actual task health status (not just "Running")
docker service ps app_backend --no-trunc
# Look for: "Health: unhealthy" or tasks recently restarted

# Step 2: Check service events and health check details
docker service inspect app_backend --pretty
# Look at HealthCheck section — is it configured? What's the threshold?

# Step 3: Identify which specific tasks (replicas) are unhealthy
# List all tasks with their node assignment
docker service ps app_backend \
  --format "table {{.ID}}\t{{.Node}}\t{{.CurrentState}}\t{{.DesiredState}}\t{{.Error}}"

# Step 4: Inspect logs for a SPECIFIC task (not just the service)
# Get the task ID from the output above (e.g. abc123def456)
docker inspect abc123def456 --format '{{.Status.ContainerStatus.ContainerID}}'
# Then on the NODE where that task runs:
docker logs <container-id> --tail 100 --follow

# Step 5: Cross-check which node each task runs on
docker node ls
docker service ps app_backend --filter "desired-state=running"

# Step 6: Check if the issue is load balancer routing to unhealthy tasks
# Test each replica directly (bypass VIP)
docker service ps app_backend --format "{{.Node}}" | while read node; do
  echo "Testing node: $node"
  docker -H ssh://$node run --rm --network app_backend_net curlimages/curl \
    -sf http://backend:4000/health
done

# Step 7: Force-restart a specific problematic replica
# Option A: Remove and let Swarm reschedule
docker service update --force app_backend
# (this re-creates ALL tasks — use sparingly)

# Option B: Target a specific node — drain it then un-drain
docker node update --availability drain <node-id>
# Wait for tasks to migrate
docker node update --availability active <node-id>
```

**Root cause checklist:**
- [ ] Health check endpoint returns 200 but application is stuck in loop?
- [ ] Memory leak causing gradual degradation? (check `docker stats`)
- [ ] Database connection pool exhausted? (check Postgres max_connections)
- [ ] External dependency (Redis, 3rd-party API) intermittently failing?
- [ ] Clock skew causing JWT validation failures?

---

### Scenario 2: Services stuck in "starting" state after stack update

```bash
# Step 1: Check task failure reasons (most useful command)
docker service ps app_backend --no-trunc
# The "Error" column often contains the reason

# Common error messages and their causes:
# "No such image" → image not pushed to registry, or auth failure
# "port already allocated" → host port conflict (use ingress mode, not host mode)
# "constraint not satisfied" → node label missing, no eligible nodes
# "no suitable node" → resource constraints too high for available nodes
# "OCI runtime error" → Dockerfile CMD/ENTRYPOINT issue, or missing file

# Step 2: Get detailed task history
docker service ps app_backend --no-trunc --filter "desired-state=shutdown"
# Shows failed/shutdown tasks with full error messages

# Step 3: Check resource availability on all nodes
docker node ls
docker node inspect <node-id> --pretty | grep -A10 "Resources"

# Check if reservations exceed available capacity:
# sum of all service reservations > node capacity → tasks can't schedule

# Step 4: Check if image is pullable on worker nodes
docker service inspect app_backend --format '{{.Spec.TaskTemplate.ContainerSpec.Image}}'
# Then on a worker node:
docker pull <that-image>

# Step 5: Check registry credentials
# Swarm workers need registry auth. Use `docker login` on all nodes OR
# use --with-registry-auth flag when deploying
docker stack deploy --with-registry-auth -c docker-compose.yml app

# Step 6: Check placement constraints
docker service inspect app_backend --format '{{json .Spec.TaskTemplate.Placement}}' | python3 -m json.tool
# Verify the required node labels actually exist:
docker node ls -q | xargs -I{} docker node inspect {} --format "{{.ID}} {{json .Spec.Labels}}"

# Step 7: Review events for detailed scheduling failures
docker events --filter type=service --filter event=update --since 1h
docker events --filter type=task --since 1h | grep -i fail
```

**Common root causes:**
1. **Image pull failure**: Registry auth not propagated to workers → use `--with-registry-auth`
2. **Resource exhaustion**: Reservations exceed available node capacity → reduce reservations or add nodes
3. **Placement constraint mismatch**: No node matches `node.labels.app==backend` → check node labels
4. **Secret/config not found**: Referenced secret was deleted → re-create it
5. **Volume mount failure**: Host path doesn't exist on target node → pre-create path or use named volumes
6. **Network not created**: Overlay network missing → run `docker network ls`

---

## Part 5.3 — Swarm Cluster Management Runbook

### Node Management

```bash
# ── Initialize / Join ──────────────────────────────────────────────────────

# Initialize a new Swarm on the first manager
docker swarm init --advertise-addr <MANAGER-IP>

# Get join token for workers
docker swarm join-token worker

# Get join token for additional managers
docker swarm join-token manager

# Add a worker node (run on the new node)
docker swarm join --token <WORKER-TOKEN> <MANAGER-IP>:2377

# Add a manager node (run on the new node)
docker swarm join --token <MANAGER-TOKEN> <MANAGER-IP>:2377

# ── Promote / Demote ───────────────────────────────────────────────────────

# Promote a worker to manager (run on existing manager)
# Best practice: always maintain an ODD number of managers (1, 3, 5, 7)
docker node promote <node-id>

# Demote a manager to worker (safely, first ensure quorum is maintained)
docker node demote <node-id>

# ── Node Labels (for placement constraints) ────────────────────────────────

# Label a node for the backend workload
docker node update --label-add app=backend --label-add zone=us-east-1a <node-id>

# Label a node for database (high-IOPS storage)
docker node update --label-add postgres=true <node-id>

# List all node labels
docker node ls -q | xargs -I{} docker node inspect {} \
  --format "{{.Description.Hostname}}: {{json .Spec.Labels}}"

# ── Drain Nodes for Maintenance ────────────────────────────────────────────

# Drain: Swarm migrates all tasks off this node gracefully
# Tasks move to healthy nodes before the node goes offline
docker node update --availability drain <node-id>

# Monitor task migration
watch 'docker service ps $(docker service ls -q) | grep <node-hostname>'

# Return node to service after maintenance
docker node update --availability active <node-id>

# Remove a node from the Swarm entirely
docker node rm <node-id>         # Requires node to be drained first
# On the node itself (if still reachable):
docker swarm leave

# ── Quorum Recovery ────────────────────────────────────────────────────────
#
# Quorum requires (N/2 + 1) managers to be reachable.
# 1 manager:  quorum = 1  (no fault tolerance)
# 3 managers: quorum = 2  (tolerates 1 failure)
# 5 managers: quorum = 3  (tolerates 2 failures)
# 7 managers: quorum = 4  (tolerates 3 failures)
#
# If quorum is LOST (majority of managers unreachable):

# Check current manager status
docker node ls | grep -i manager

# Force a new cluster from surviving data (LAST RESORT — data loss risk)
# Run on a surviving manager node:
docker swarm init --force-new-cluster --advertise-addr <SURVIVING-MANAGER-IP>

# Immediately add new managers to restore quorum
docker swarm join-token manager  # Get new token
# Add 2 more managers if doing 3-node HA

# ── Backup & Restore ────────────────────────────────────────────────────────

# Backup Swarm state (Raft log — includes all secrets, configs, service definitions)
# Must be run on a MANAGER node while Swarm is running
BACKUP_DATE=$(date +%Y%m%d-%H%M%S)
sudo tar -czf "swarm-backup-${BACKUP_DATE}.tar.gz" \
  -C /var/lib/docker/swarm .

# Secure the backup (contains encrypted secrets)
gpg --symmetric --cipher-algo AES256 "swarm-backup-${BACKUP_DATE}.tar.gz"

# Restore Swarm state
# 1. Stop Docker on all nodes
sudo systemctl stop docker

# 2. Restore the Raft data on ONE manager only
sudo rm -rf /var/lib/docker/swarm
sudo tar -xzf "swarm-backup-${BACKUP_DATE}.tar.gz" -C /var/lib/docker/swarm

# 3. Start Docker and force-initialize from backup
sudo systemctl start docker
docker swarm init --force-new-cluster --advertise-addr <MANAGER-IP>

# 4. Re-join other nodes (they need new tokens since Raft log was restored)
docker swarm join-token worker   # Get new worker token
docker swarm join-token manager  # Get new manager token
```

### Scheduling Best Practices

```bash
# Use placement preferences to SPREAD replicas across failure domains
# This is a soft preference (Swarm tries but won't fail if impossible)
docker service update \
  --placement-pref-add "spread=node.labels.zone" \
  app_backend

# Use placement CONSTRAINTS for hard requirements
# (service will ONLY run on nodes matching this)
docker service update \
  --constraint-add "node.labels.disk==ssd" \
  app_postgres

# Reserve resources to prevent noisy-neighbor problems
docker service update \
  --reserve-cpu 0.25 \
  --reserve-memory 256m \
  --limit-cpu 1.0 \
  --limit-memory 512m \
  app_backend
```
