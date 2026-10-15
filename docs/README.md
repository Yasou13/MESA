# MESA Core documentation

These guides describe the checked-out V4 implementation and V3 compatibility
surface. Package version is 0.8.0; publication is owner-approved with the
[documented certification limitations](release-notes-v0.8.0.md).
Production status is `NO-GO` under the existing release policy.

| Journey | Guide |
|---|---|
| First scoped insert and search | [Getting started](getting-started.md) |
| Install and deploy | [Installation](installation.md) |
| Understand modules and storage | [Architecture](architecture-v4.md) |
| Catalog, versions and mutations | [Concepts and ingestion](concepts-and-ingestion.md) |
| Configure providers and bounds | [Configuration](configuration.md) |
| HTTP and Python interfaces | [API and SDK](api-reference.md) |
| Operator and integration commands | [CLI](cli-reference.md), [MCP](../README_MCP.md) |
| Ranking and evidence | [Retrieval and graph](retrieval-and-graph.md) |
| Operate and recover | [Runbook](RUNBOOK.md), [rebuild](v4-rebuild-runbook.md) |
| Secure a deployment | [Security and privacy](security-and-privacy.md), [reporting](../SECURITY.md) |
| Diagnose failures | [Troubleshooting](troubleshooting.md) |
| Capacity and unsupported behavior | [Performance and limitations](performance-and-limitations.md) |
| External projects | [Ecosystem](ecosystem.md) |
| Upgrade safely | [Migration to v0.8.0](migration-v0.8.0.md) |
| Extend and verify | [Contributing](../CONTRIBUTING.md), [testing](testing.md), [CI](ci-assurance.md) |
| Release readiness | [Release procedure](release.md), [release notes](release-notes-v0.8.0.md) |
| Local control panel | [Control panel](Mesa_Control_Panel.md), [dashboard](../mesa_dashboard/README.md) |
| Notebook experimentation | [Colab](colab_kurulum_rehberi.md) |

Existing guides use English and Turkish. This index identifies the canonical
page for each subject; a full translation of every guide is not offered.
The static showcase provides synchronized short EN/TR summaries.

## Historical records

[Root architecture](../ARCHITECTURE.md), [past benchmarks](historical_benchmarks/BENCHMARK_INTEGRITY_LOG.md),
[the previous audit](v4-final-independent-audit.md), previous changelog sections
and completed reports retain their original claims. Use current guides for
operations. ADRs describe decisions and supersession, not current measurements.
