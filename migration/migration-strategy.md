# Migration Strategy: Docker Swarm → Kubernetes

## Executive Summary

This document outlines a phased migration strategy for moving our microservices
application stack from Docker Swarm to Kubernetes. The migration is driven by
the need for richer scheduling primitives, a broader ecosystem, and access to
managed Kubernetes offerings (EKS, GKE, AKS). We recommend a **phased,
parallel-run migration** over a big-bang approach to minimize risk and ensure
production continuity.

---

## 1. Feature Mapping: Swarm → Kubernetes Equivalents

| Docker Swarm Concept | Kubernetes Equivalent | Notes |
|---|---|---|
| `docker stack deploy` | `helm install` / `kubectl apply` | Helm is the de-facto stack manager |
| `service` (replicated) | `Deployment` | StatefulSet for stateful workloads |
| `service` (global) | `DaemonSet` | node-exporter, cadvisor |
| `service` (replicated, 1 replica, ordered) | `StatefulSet` | Postgres, Redis with ordered startup |
| Swarm secret | `Secret` (type Opaque) | K8s secrets base64-encoded; use Sealed Secrets or Vault for encryption at rest |
| Swarm config | `ConfigMap` | Same concept, different API |
| Overlay network | `NetworkPolicy` | K8s has flat network by default; NetworkPolicy adds egress/ingress rules |
| Placement constraint | `nodeSelector` / `nodeAffinity` | `requiredDuringScheduling` = hard constraint |
| Placement preference (spread) | `podAntiAffinity` | `preferredDuringScheduling` = soft preference |
| Resource limits/reservations | `resources.limits` / `resources.requests` | Identical semantics |
| Rolling update config | `strategy.rollingUpdate` in Deployment | `maxUnavailable`, `maxSurge` |
| Automatic rollback | `progressDeadlineSeconds` + liveness probes | K8s pauses rollout on probe failure |
| Health check | `livenessProbe` + `readinessProbe` | K8s separates "alive" from "ready to serve traffic" |
| Named volume | `PersistentVolumeClaim` (PVC) | Backed by PV provisioned by StorageClass |
| Traefik labels | `Ingress` / `IngressClass` (or Traefik CRDs) | Traefik works in K8s too; Nginx Ingress is common alternative |
| Swarm TLS (mTLS between nodes) | Istio / Linkerd service mesh | K8s has no built-in mTLS; service mesh adds it |

---

## 2. Migration Approach: Phased vs. Big-Bang

### Why NOT Big-Bang

A big-bang migration (flip all services at once) carries unacceptable risk:
- No rollback path once DNS is cut over
- Operations team must learn Kubernetes tooling simultaneously with managing a production incident
- Databases migrated in one window with full downtime

### Recommended: Phased Migration (Traffic-Splitting)

We migrate service by service, running Swarm and Kubernetes in parallel, with
a load balancer gradually shifting traffic.

```
Phase 0 (2 weeks):  Preparation — K8s cluster provisioning, tooling, runbooks
Phase 1 (2 weeks):  Stateless services — frontend & backend (non-critical traffic 10%)
Phase 2 (2 weeks):  Increase traffic split to 50/50, validate observability
Phase 3 (1 week):   Stateful services — Redis, then Postgres (online migration)
Phase 4 (1 week):   Full cutover — 100% traffic to K8s, Swarm in standby
Phase 5 (2 weeks):  Swarm decommission after stability verified
```

**Total: ~10 weeks with rollback capability at every phase.**

---

## 3. Tooling

### Kompose (Automated Conversion — Starting Point Only)

```bash
# Install Kompose
curl -L https://github.com/kubernetes/kompose/releases/download/v1.33.0/kompose-linux-amd64 -o kompose
chmod +x kompose

# Convert docker-compose.yml to K8s manifests
kompose convert -f docker-compose.yml --out ./k8s-manifests/

# Output: Deployment, Service, PVC YAMLs per service
# WARNING: Kompose output requires significant manual review:
# - Secrets become plain ConfigMaps (insecure)
# - No NetworkPolicies generated
# - Resource limits may be off
# - Health checks need readinessProbe/livenessProbe split
```

### Manual Conversion (Production Quality)

Kompose gives a scaffold. For production, manually author Helm charts (see Part 6.2)
which provide:
- Parameterization via `values.yaml` (no env-specific manifests)
- Rollback via `helm rollback`
- Templating for DRY multi-environment configs

### Secrets Migration

```bash
# Convert Swarm secret to K8s Secret
SWARM_SECRET_VALUE=$(docker secret inspect db_password --format '{{.Spec.Data}}')
kubectl create secret generic db-credentials \
  --from-literal=password="${SWARM_SECRET_VALUE}" \
  --namespace=production

# Preferred: Use Sealed Secrets for GitOps-safe encrypted secrets
kubeseal --format=yaml < k8s-secrets.yaml > sealed-secrets.yaml
# sealed-secrets.yaml is safe to commit to Git
```

---

## 4. Database Migration (Most Critical Path)

PostgreSQL is the highest-risk component. Options in order of preference:

**Option A: Logical Replication (Zero-Downtime)**
1. Set up PostgreSQL in K8s as a StatefulSet
2. Configure Swarm Postgres as publisher, K8s Postgres as subscriber using `pg_logical` or built-in logical replication
3. Let replication catch up (monitor lag with `pg_replication_slots`)
4. Switch application connection strings in a config update (seconds of downtime)
5. Promote K8s Postgres to primary, drop replication slot

**Option B: pg_dump with maintenance window (Simplest)**
1. Announce maintenance window (30–60 min)
2. Scale backend to 0 replicas to quiesce writes
3. `pg_dump` → upload to S3 → `pg_restore` into K8s Postgres
4. Update connection strings and scale backend back up

---

## 5. Testing Strategy

### Parity Validation

Before cutting traffic, validate that K8s environment is functionally identical:

```bash
# 1. Smoke tests (critical paths only)
./scripts/smoke-test.sh --env k8s-staging

# 2. Load test at production traffic levels
k6 run --vus 100 --duration 10m scripts/load-test.js

# 3. Chaos testing (kill random pods, verify self-healing)
kubectl delete pod -l app=backend --force  # Should self-heal in <30s

# 4. Observability parity
# Verify same metrics, dashboards, and alert rules fire correctly in K8s

# 5. Secret rotation test (ensure application reads new secret without restart)
kubectl create secret generic db-credentials --dry-run=client \
  --from-literal=password=newpassword -o yaml | kubectl apply -f -
```

### Canary Validation Checklist

- [ ] All health check endpoints return 200
- [ ] Database reads/writes succeed
- [ ] Redis cache hit/miss rates normal
- [ ] P99 API latency within 10% of Swarm baseline
- [ ] Error rate < 0.1%
- [ ] All Prometheus metrics visible in Grafana
- [ ] Alerts fire correctly in Alertmanager
- [ ] TLS certificates valid and auto-renewing

---

## 6. Rollback Plan

At each phase, a documented rollback procedure exists:

**Phases 0–2 (stateless services):** Update load balancer weights to route 100% back to Swarm. No data migration needed. ETA: 5 minutes.

**Phase 3 (stateful services):** If using logical replication, promote Swarm Postgres back to primary and update connection strings. ETA: 10–30 minutes.

**Phase 4+ (post-cutover):** Swarm stack remains deployed but scaled to 0. To rollback:
```bash
# Scale Swarm services back up
docker service scale app_backend=3 app_frontend=3

# Update DNS / load balancer weights
# Re-verify with smoke tests
```

**Never decommission Swarm** (Phase 5) until K8s has been stable in production for at least 2 weeks.

---

## 7. Risk Assessment & Timeline

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Database data loss during migration | Low | Critical | Use logical replication; full backup before any migration step |
| K8s cluster misconfiguration causing outage | Medium | High | Phased rollout; Swarm stays live until K8s verified |
| Secret management gaps (Swarm → K8s) | Medium | High | Audit all secrets; use Sealed Secrets from Day 1 |
| Operations team unfamiliar with kubectl | Medium | Medium | 2-week training period in Phase 0; runbooks for all operations |
| Kubernetes version upgrade cadence | Low | Low | Use managed K8s (EKS/GKE/AKS) for automated upgrades |
| Increased infrastructure cost during parallel run | High | Low | Budget for 2x infra for ~6 weeks; acceptable for migration safety |

**Estimated total migration timeline: 10–12 weeks**

---

## 8. Post-Migration Benefits

After successful migration to Kubernetes:

- **Horizontal Pod Autoscaling (HPA)**: Automatic replica scaling based on CPU/memory/custom metrics — not possible in Swarm without manual intervention
- **Cluster Autoscaler**: Nodes added/removed automatically based on pending pods
- **Richer health probes**: Separate `livenessProbe` (restart if unhealthy) and `readinessProbe` (remove from LB if not ready) and `startupProbe`
- **GitOps with ArgoCD/Flux**: Declarative, Git-driven deployments with automatic drift detection
- **Service mesh (Istio)**: Automatic mTLS, traffic splitting, circuit breaking, distributed tracing
- **Managed upgrades**: Cloud providers handle control-plane upgrades with minimal downtime
