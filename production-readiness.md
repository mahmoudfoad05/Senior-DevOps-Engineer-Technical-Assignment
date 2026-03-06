# Production Readiness Checklist — Task 8.2

## 1. High Availability & Manager Quorum

- [ ] Deploy an **odd number of manager nodes** (3 or 5 for production)
  - 3 managers: tolerates 1 failure (quorum = 2)
  - 5 managers: tolerates 2 failures (quorum = 3)
  - Never run 2 or 4 managers (even numbers provide no extra fault tolerance)
- [ ] Spread managers across **availability zones** or physical racks
  ```bash
  # Label nodes with their AZ
  docker node update --label-add zone=us-east-1a manager-1
  docker node update --label-add zone=us-east-1b manager-2
  docker node update --label-add zone=us-east-1c manager-3
  ```
- [ ] Verify quorum health: `docker node ls` — all managers should show `Leader` or `Reachable`
- [ ] Configure manager nodes as **manager-only** (no application workloads):
  ```bash
  docker node update --availability drain manager-1  # Drain workloads
  # Or in compose: constraints: [node.role == worker]
  ```
- [ ] Test quorum recovery: simulate manager failure and verify cluster continues operating
- [ ] Document quorum recovery procedure (see troubleshooting runbook)

---

## 2. Service Reliability

- [ ] All services have **health checks** configured with appropriate intervals
- [ ] All services have `restart_policy` configured (condition, delay, max_attempts)
- [ ] `update_config.failure_action: rollback` set for all critical services
- [ ] `update_config.order: start-first` for stateless services to ensure zero downtime
- [ ] All services have **resource limits AND reservations** set
- [ ] Verify Swarm auto-restarts failed tasks: `docker service ps <n> | grep Shutdown`
- [ ] Test rolling update and automatic rollback in staging before production
- [ ] Minimum 2 replicas for all stateless services (frontend, backend)

---

## 3. Backup & Disaster Recovery

- [ ] **Daily automated backup** of Swarm Raft state (`/var/lib/docker/swarm`)
  ```bash
  # Automated backup cron job on manager nodes
  0 2 * * * tar -czf /backup/swarm-$(date +%Y%m%d).tar.gz /var/lib/docker/swarm
  ```
- [ ] **Database backups**: daily full + hourly WAL archiving (PostgreSQL)
  ```bash
  # pg_basebackup for binary backup
  pg_basebackup -h postgres -U backup_user -D /backup/postgres -Ft -z
  # Or pg_dump for logical backup
  pg_dump -h postgres -U appuser appdb | gzip > /backup/appdb-$(date +%Y%m%d).sql.gz
  ```
- [ ] Backup retention policy defined and enforced (e.g., 7 daily, 4 weekly, 12 monthly)
- [ ] Backups stored **off-cluster** (S3, GCS, or off-site storage)
- [ ] Backup integrity verified weekly (test restore to staging)
- [ ] **RTO and RPO documented** and validated:
  - RPO (max acceptable data loss): target < 1 hour with WAL archiving
  - RTO (max acceptable downtime): target < 30 minutes with documented procedure
- [ ] Disaster recovery runbook written, versioned, and rehearsed (at least annually)
- [ ] Runbook stored OUTSIDE of the cluster being recovered

---

## 4. Maintenance Procedures

- [ ] Document and test **node drain procedure** before any maintenance:
  ```bash
  # Pre-maintenance: drain node (migrates all tasks)
  docker node update --availability drain <node-id>

  # Verify all tasks have migrated
  docker service ps $(docker service ls -q) | grep <node-hostname>

  # Perform maintenance (patch OS, reboot, hardware work)

  # Post-maintenance: return to service
  docker node update --availability active <node-id>
  ```
- [ ] Define **maintenance windows** and communicate to stakeholders
- [ ] Test that draining a node doesn't cause service disruptions (verify replica count)
- [ ] Keep Docker Engine version within 2 major versions of current release
- [ ] Document OS update procedure (kernel updates require node reboot)

---

## 5. Capacity Planning

- [ ] **Current utilization baseline** documented (CPU, memory, network per service)
  ```bash
  docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}"
  ```
- [ ] Capacity headroom: nodes should run at < 70% CPU and < 80% memory under normal load
- [ ] **Auto-scaling trigger**: if a service consistently needs manual scaling, evaluate HPA (K8s)
- [ ] Node count scaling plan documented (at what load level to add nodes)
- [ ] Storage capacity monitoring and alerts configured (alert at 80% disk usage)
- [ ] Network bandwidth capacity assessed for peak traffic scenarios
- [ ] Load test at 2x expected peak traffic to validate headroom

---

## 6. Observability Requirements

- [ ] Prometheus collecting metrics from ALL services (via service labels)
- [ ] Grafana dashboards configured for:
  - [ ] Service replica count and health (real-time)
  - [ ] Node CPU / memory / disk usage
  - [ ] Container restart count (early warning of crash loops)
  - [ ] API response time P50/P95/P99
  - [ ] Database query performance and connection pool usage
  - [ ] Error rate per service
- [ ] **Alerting rules** configured and tested (see `monitoring/alerts/swarm.yml`)
- [ ] On-call rotation established with PagerDuty or equivalent
- [ ] Alert runbooks linked from each alert rule (how to investigate and resolve)
- [ ] Centralized logging (Fluentd or Loki) collecting from all containers
- [ ] Log retention policy set (e.g., 30 days hot, 1 year cold)
- [ ] Distributed tracing configured (Jaeger or Zipkin) for request flow analysis

---

## 7. Deployment Process

- [ ] All deployments via CI/CD pipeline (no manual `docker service update` in production)
- [ ] Production deployments require **manual approval gate**
- [ ] Deployment notifications sent to team Slack channel
- [ ] Rollback procedure documented and tested (< 5 minute execution time)
- [ ] Staging environment mirrors production configuration (validated parity)
- [ ] Feature flags available for risky features (decouple deploy from release)
- [ ] **Deployment window** defined (avoid deploy during peak traffic unless critical)
- [ ] Post-deployment validation automated (smoke tests in CI/CD)

---

## 8. Documentation

- [ ] Architecture diagram up-to-date (network topology, service dependencies)
- [ ] README with step-by-step deployment instructions (tested by someone other than author)
- [ ] Runbooks for all common operational tasks (drain node, rotate secret, scale service)
- [ ] Incident response playbook (escalation path, communication templates)
- [ ] On-call handbook with login credentials securely stored (password manager)
- [ ] Known issues and workarounds documented
- [ ] Dependency versions tracked (Docker, OS, all service images)

---

## Pre-Deployment Sign-Off Checklist

Before deploying a new service version to production, confirm:

```
[ ] Staging deployment succeeded and smoke tests passed
[ ] Security scan shows no new CRITICAL/HIGH CVEs
[ ] Load test results are within acceptable bounds
[ ] On-call engineer is available (not during off-hours without emergency)
[ ] Rollback procedure is documented and tested
[ ] Stakeholders notified of maintenance window (if required)
[ ] Monitoring dashboards are open and baseline established
[ ] Database migration is backward-compatible (if applicable)
```
