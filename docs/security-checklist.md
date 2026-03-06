# Docker Swarm Security Hardening Checklist — Task 8.1

## 1. Secrets Management

- [ ] **Use Swarm secrets, NEVER environment variables** for sensitive data
  ```yaml
  # ✅ CORRECT — secret mounted as file at /run/secrets/db_password
  secrets:
    - db_password
  # App reads: open('/run/secrets/db_password').read().strip()

  # ❌ WRONG — visible in `docker inspect`, logs, and `docker service inspect`
  environment:
    - DB_PASSWORD=mysecretpassword
  ```
- [ ] Rotate secrets with zero downtime using versioned secret names (see `scripts/secrets_setup.sh rotate`)
- [ ] Set least-privilege secret access — only mount secrets in services that need them
- [ ] Audit secret access: `docker secret ls` and `docker secret inspect <name>` (shows which services use it)
- [ ] Never store secrets in Docker images or Dockerfiles
- [ ] Use `--secret` in `docker build` for build-time secrets (BuildKit), not `ARG`

---

## 2. Network Security

- [ ] **Encrypt all overlay networks** with `driver_opts: encrypted: "true"`
  ```bash
  docker network create --driver overlay --opt encrypted app_backend_net
  ```
- [ ] **Segment networks by trust tier** — don't put frontend and database on the same network
- [ ] Use `attachable: false` on production networks to prevent ad-hoc containers from joining
- [ ] Implement ingress-only exposure: only Traefik/Nginx should be on the `public` network
- [ ] Verify encryption is active: `docker network inspect <network> | grep -i encrypt`
- [ ] Block direct access to non-public ports using host firewall (iptables/nftables)
  ```bash
  # Only allow external access to 80 and 443
  iptables -A INPUT -p tcp --dport 80 -j ACCEPT
  iptables -A INPUT -p tcp --dport 443 -j ACCEPT
  # Block direct access to service ports (4000, 5432, 6379, etc.)
  iptables -A INPUT -p tcp --dport 4000 -j DROP
  ```

---

## 3. Image Security

- [ ] **Use specific image tags** (never `latest` in production — it's mutable and unauditable)
  ```yaml
  # ✅ Pinned SHA for total reproducibility
  image: postgres:16-alpine@sha256:abc123...
  # ✅ Acceptable: specific version tag
  image: postgres:16.2-alpine
  # ❌ Never in production
  image: postgres:latest
  ```
- [ ] Scan all images with Trivy before deployment (see CI/CD pipeline)
- [ ] Enable Docker Content Trust (image signing)
  ```bash
  export DOCKER_CONTENT_TRUST=1
  ```
- [ ] Use minimal base images: `alpine` or `distroless` over full `ubuntu`/`debian`
- [ ] Run as non-root user in all Dockerfiles (`USER nodeapp`)
- [ ] Use read-only root filesystem where possible:
  ```yaml
  deploy:
    labels: []
  # In service definition:
  read_only: true
  tmpfs:
    - /tmp
    - /var/run
  ```
- [ ] Only pull from trusted registries; configure allowlist in Docker daemon

---

## 4. Swarm API Access Control

- [ ] **Protect the Docker socket** — never expose `/var/run/docker.sock` to untrusted services
- [ ] Enable TLS for the Docker API (required for remote management)
  ```bash
  # Generate CA, server cert, and client cert
  # Then configure Docker daemon:
  # /etc/docker/daemon.json
  {
    "tls": true,
    "tlscacert": "/etc/docker/ca.pem",
    "tlscert":   "/etc/docker/server-cert.pem",
    "tlskey":    "/etc/docker/server-key.pem",
    "tlsverify": true,
    "hosts":     ["tcp://0.0.0.0:2376", "unix:///var/run/docker.sock"]
  }
  ```
- [ ] Restrict Swarm manager API port (2377) access to management network only
- [ ] Use certificate-based authentication for Portainer
- [ ] Rotate Swarm join tokens regularly:
  ```bash
  docker swarm join-token --rotate worker
  docker swarm join-token --rotate manager
  ```

---

## 5. Resource Isolation

- [ ] Set CPU and memory limits on ALL services (prevents noisy-neighbor)
  ```yaml
  resources:
    limits:
      cpus: "1.0"
      memory: 512M
    reservations:
      cpus: "0.25"
      memory: 256M
  ```
- [ ] Set `--default-ulimit` on Docker daemon for nofile, nproc limits
- [ ] Use dedicated nodes for database workloads (node labels + constraints)
- [ ] Enable resource accounting: `docker stats` / cAdvisor
- [ ] Configure OOM kill priority with `--oom-score-adj` if needed for critical services

---

## 6. Audit Logging

- [ ] Enable Docker daemon audit logging
  ```bash
  # /etc/docker/daemon.json
  {
    "log-driver": "json-file",
    "log-opts": {
      "max-size": "10m",
      "max-file": "5",
      "labels": "service,environment"
    }
  }
  ```
- [ ] Ship Docker daemon logs to centralized SIEM (Splunk, ELK, CloudWatch)
- [ ] Log Portainer API access (who deployed what and when)
- [ ] Track `docker service update` events:
  ```bash
  docker events --filter type=service --since 2024-01-01 | grep -E "update|create|remove"
  ```
- [ ] Audit container privilege escalation attempts via `auditd` on host

---

## 7. Node / OS Hardening

- [ ] Apply CIS Docker Benchmark (use `docker-bench-security` tool):
  ```bash
  docker run --net host --pid host --userns host --cap-add audit_control \
    -v /etc:/etc:ro -v /var/lib:/var/lib:ro \
    -v /var/run/docker.sock:/var/run/docker.sock:ro \
    docker/docker-bench-security
  ```
- [ ] Disable unused Linux kernel capabilities:
  ```yaml
  cap_drop: [ALL]
  cap_add: [NET_BIND_SERVICE]  # Only if binding to privileged ports
  ```
- [ ] Enable AppArmor or SELinux profiles for containers
- [ ] Keep Docker Engine and OS updated (subscribe to CVE notifications)
- [ ] Use `--no-new-privileges` to prevent setuid/setgid escalation
  ```yaml
  security_opt:
    - no-new-privileges:true
  ```
- [ ] Restrict host filesystem access — never mount `/` or `/proc` in production services
