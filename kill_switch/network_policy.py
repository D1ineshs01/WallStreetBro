"""
Network Layer Kill Switch — eBPF/Cilium/Kubernetes Implementation Stub

OVERVIEW
========
This module documents the enterprise-grade network isolation strategy
using Kubernetes + Cilium CNI + eBPF for kernel-level traffic control.

The application-layer kill switch (Redis key) is sufficient for most deployments.
This network layer adds an UNBREACHABLE second line of defense for containerized
production environments, satisfying the most stringent institutional requirements
(ASIC RG 241, MiFID II Article 17).

ARCHITECTURE
============

Kubernetes Namespace: wallstreetbro
  ├── Deployment: execution-agent   (label: app=execution-agent)
  ├── Deployment: fastapi-backend   (label: app=fastapi-backend)
  ├── Deployment: grok-scanner      (label: app=grok-scanner)
  └── DaemonSet:  ebpf-monitor      (label: app=ebpf-monitor)

CiliumNetworkPolicy (Default Deny + Allowlist):
  - Only execution-agent pod may reach api.alpaca.markets
  - All pods may reach Redis + PostgreSQL internally
  - All pods may reach xAI API + Anthropic API
  - DENIED when pod label quarantined=true is applied

eBPF Monitor DaemonSet:
  - Watches TCP connection rate on execution-agent pod
  - Threshold: >100 outbound connections/minute to Alpaca = anomaly
  - On anomaly: patches pod label quarantined=true
  - Cilium policy instantly denies all outbound Alpaca traffic at kernel level

TRIGGER SEQUENCE
================
1. KillSwitchMonitor detects breach (application layer)
   → Sets Redis agent:execution_status=False
   → Calls trading.cancel_orders()
   → ALSO calls apply_quarantine_label() in this module

2. If application crashes and loop continues anyway:
   → eBPF monitor detects >100 conn/min anomaly
   → Applies quarantine label independently
   → Cilium drops all outbound Alpaca packets at kernel level

3. Human operator can resume by:
   → Removing quarantine label: kubectl label pod ... quarantined-
   → Setting Redis: redis-cli SET agent:execution_status 1
   → Both must be reset (defense in depth)

KUBERNETES YAML FILES REQUIRED
================================
Create these files in k8s/ directory before deploying to Kubernetes:

  k8s/namespace.yaml
  k8s/network-policy-base.yaml       # Base CiliumNetworkPolicy (allow internal)
  k8s/network-policy-quarantine.yaml # Policy denying egress when quarantined=true
  k8s/execution-deployment.yaml      # Execution agent Deployment
  k8s/ebpf-monitor-daemonset.yaml    # eBPF monitoring DaemonSet
  k8s/quarantine-rbac.yaml           # RBAC for pod labeling

CILIUM NETWORK POLICY PSEUDOCODE
==================================

# Base policy: allow execution-agent to reach Alpaca
apiVersion: cilium.io/v2
kind: CiliumNetworkPolicy
metadata:
  name: allow-alpaca-egress
  namespace: wallstreetbro
spec:
  endpointSelector:
    matchLabels:
      app: execution-agent
      quarantined: "false"       # Only applies when NOT quarantined
  egress:
  - toFQDNs:
    - matchName: api.alpaca.markets
    - matchName: paper-api.alpaca.markets
  - toPorts:
    - ports:
      - port: "443"
        protocol: TCP

---
# Quarantine policy: deny ALL egress when label applied
apiVersion: cilium.io/v2
kind: CiliumNetworkPolicy
metadata:
  name: deny-quarantine-egress
  namespace: wallstreetbro
spec:
  endpointSelector:
    matchLabels:
      quarantined: "true"
  egressDeny:
  - toEntities:
    - world              # Blocks all internet traffic at kernel level
"""

import subprocess
from typing import Optional


def get_quarantine_command(pod_name: str, namespace: str = "wallstreetbro") -> str:
    """
    Returns the kubectl command to quarantine a specific pod.
    Does NOT execute it — call apply_quarantine_label() to run.
    """
    return f"kubectl label pod {pod_name} quarantined=true -n {namespace} --overwrite"


def get_unquarantine_command(pod_name: str, namespace: str = "wallstreetbro") -> str:
    """Returns the kubectl command to lift the quarantine from a pod."""
    return f"kubectl label pod {pod_name} quarantined- -n {namespace}"


def apply_quarantine_label(
    pod_name: str,
    namespace: str = "wallstreetbro",
    dry_run: bool = True,
) -> Optional[str]:
    """
    Apply the quarantine label to a pod via kubectl.
    By default runs in dry_run=True mode for safety.
    Set dry_run=False only in production Kubernetes environments.

    Returns the command output or None on error.
    """
    cmd = get_quarantine_command(pod_name, namespace)
    if dry_run:
        print(f"[DRY RUN] Would execute: {cmd}")
        return cmd

    try:
        result = subprocess.run(
            cmd.split(), capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            return result.stdout
        else:
            print(f"[ERROR] kubectl failed: {result.stderr}")
            return None
    except FileNotFoundError:
        print("[WARNING] kubectl not found — network layer kill switch unavailable")
        return None
    except subprocess.TimeoutExpired:
        print("[ERROR] kubectl timed out — pod may not be quarantined")
        return None


def get_execution_pod_name(namespace: str = "wallstreetbro") -> Optional[str]:
    """
    Discover the current execution-agent pod name via kubectl.
    Returns None if kubectl is not available or pod not found.
    """
    try:
        result = subprocess.run(
            [
                "kubectl", "get", "pods",
                "-n", namespace,
                "-l", "app=execution-agent",
                "-o", "jsonpath={.items[0].metadata.name}",
            ],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout:
            return result.stdout.strip()
        return None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
