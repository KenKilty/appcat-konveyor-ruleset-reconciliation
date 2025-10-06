---
title: adopt-appcat-ruleset-enhancements
authors:
  - "@Azure-AppCAT-Migration"
reviewers:
  - TBD
approvers:
  - TBD
creation-date: 2025-10-06
last-updated: 2025-10-06
status: provisional
see-also:
  - "/enhancements/guidelines/enhancement_template.md"
---

# Adopt [Microsoft AppCAT ruleset](https://github.com/Azure/appcat-konveyor-rulesets) enhancements in Konveyor

## Summary

AppCAT extends Konveyor rules with optional metadata and stronger detection logic tailored for cloud migrations. This enhancement request upstreams those additive improvements as an opt‑in superset schema and matching logic so the broader Konveyor community benefits without breaking existing users.

Key additions:
- Optional metadata: `message`, `domain`, `category`, `metadata{severity,confidence,migrationPath}`
- Improved detection logic: anchored regexes, multi-signal conditions (file, dependencies, references), and clearer messages
- Documentation and consistency improvements for rule authoring

These changes are backward compatible: existing Konveyor fields remain valid, and new fields are optional.

## Motivation

### Goals
- Increase accuracy and reduce false positives through precise regex patterns and multi-signal detection
- Improve user guidance via optional `message` and categorization fields
- Enable cloud provider downstream focused experiences without impacting existing users
- Keep Konveyor as the single upstream source of truth while allowing richer downstream usage

### Non-Goals
- Forcing downstream adopters to use new fields
- Changing `ruleID` identity semantics
- Introducing provider specific behavior that breaks generic Kubernetes use cases

## Proposal

### Unified superset schema (additive)

The following example illustrates additive fields while preserving Konveyor’s core schema:

```yaml
- ruleID: azure-cache-redis-01000
  description: "Application uses Redis cache"
  message: "Consider migrating to a managed Redis service"
  domain: "cloud"
  category: "cache"
  tag: ["Redis", "Cache"]
  labels: ["konveyor.io/include=always"]
  effort: "medium"
  links: []
  customVariables: []
  when:
    or:
      - builtin.file:
          pattern: "^([a-zA-Z0-9._-]*)redis([a-zA-Z0-9._-]*)\\.jar$"
      - java.dependency:
          name: "redis.clients.jedis"
          lowerbound: "0.0.0"
  metadata:
    severity: "medium"
    confidence: "high"
    migrationPath: "managed-redis"
```

Guidelines:
- New fields are optional; existing tools that ignore them remain unaffected
- `ruleID` remains the canonical key for matching/merging
- Detection logic can combine multiple signals using `or`/`and` constructs

### Examples

#### 1) Anchored regex vs wildcard

Before (higher false positives):

```yaml
when:
  builtin.file:
    pattern: ".*liferay.*\\.jar"
```

After (more precise):

```yaml
when:
  builtin.file:
    pattern: "^([a-zA-Z0-9._-]*)liferay([a-zA-Z0-9._-]*)\\.jar$"
```

Impact: avoids matching unrelated files that merely contain the substring.

#### 2) Multi-signal detection (file + dependency)

Before (file-only):

```yaml
when:
  builtin.file:
    pattern: "spring-boot.*\\.jar"
```

After (dependency OR file):

```yaml
when:
  or:
    - java.dependency:
        name: "org.springframework.spring-boot"
        lowerbound: "0.0.0"
    - builtin.file:
        pattern: "^spring-boot([a-zA-Z0-9._-]*)\\.jar$"
```

Impact: catches projects declaring Spring Boot even when jar names differ, and reduces false positives when jars are present but unused.

#### 3) Message and categorization

Before:

```yaml
description: "Uses S3 SDK"
```

After (optional fields add UX value):

```yaml
description: "Uses S3 SDK"
message: "Consider migrating to a managed object storage service"
domain: "cloud"
category: "object-storage"
```

Impact: improves guidance for users and downstream tooling without breaking existing processors.

### Benefits to Konveyor community
- Higher signal detection with fewer false positives by using anchored regexes and multi-signal checks
- Better UX in UIs and reports via optional `message`, `domain`, and `category`
- Provides on-ramp for easier provider focused extensions without schema forks
- Downstream innovation (e.g., Microsoft AppCAT) can flow upstream without breaking changes

### Compatibility
- 100% backward compatible: existing rules remain valid
- New fields are optional and can be safely ignored by current processors
- Where processors surface metadata, they can optionally display `message`, `domain`, `category`, and `metadata` when present

## User Stories

#### Rule author
As a rule author, I can add optional `message` and category fields to provide clearer guidance without breaking existing pipelines.

#### Migration engineer
As a migration engineer, I get more accurate findings because rules consider dependency declarations in addition to filenames or content.

#### Tooling integrator
As a tooling integrator, I can surface richer messages and categories in reports when available, while remaining compatible with older rules.

## Implementation Details / Notes
- Treat new fields as optional in readers/linters
- Encourage anchored regex patterns (`^` / `$`) when appropriate
- Encourage multi-signal conditions (e.g., `java.dependency`, `builtin.file`, `java.referenced`) for precision
- Maintain `ruleID` stability; avoid semantic changes that require renumbering

## Security, Risks, and Mitigations
- Risk: Increased rule complexity. Mitigation: keep additions optional via opt-in
- Risk: Provider managed Kubernetes bias. Mitigation: use neutral `domain` values (e.g., `cloud`) and keep rules vendor nuetral unless clearly scoped (e.g. existing Azure rules in the upstream Konveyor ruleset repository)

## Drawbacks
- Slightly more complex authoring for advanced rules
- Some processors may need small UI updates to display optional fields

## Alternatives
- Keep enhancements downstream only (fragmentation risk, no upstream benefit to Konveyor project and users)
- Fork schema (breaks ecosystem convergence and collaboration) 

## Test Plan
- Validate unchanged behavior on existing Konveyor rules (no new fields)
- Validate additive behavior when new fields are present (ignored by old processors, used by updated ones)

## Upgrade / Downgrade Strategy
- No upgrade required for consumers that ignore optional fields
- Processors that choose to surface new fields can add non-breaking display logic

## Implementation History
- 2025-10-06: Provisional proposal authored consolidating AppCAT’s additive rule improvements


