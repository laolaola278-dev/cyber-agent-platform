# Phase 16 WAF Response Plugin

## 1. Scope and trust boundary

Phase 16 validates whether the existing Response Framework can govern a first real-type integration without changing the framework. `waf-response` implements capability `response.waf`, but its provider is strictly synthetic. There is no production endpoint, credential, network client, subprocess, shell, database access, filesystem write or provider SDK.

The framework remains responsible for Plan, Policy, Approval, Runtime-exclusive execution, immutable Incident/Asset scope, Evidence persistence, Audit publication and Rollback orchestration. The plugin owns only WAF-specific rule validation, provider adaptation, verification and rollback semantics.

## 2. Unified WAFRule

| Field | Meaning |
|---|---|
| `id` | Stable CAP-managed identifier; provider-owned prefixes are rejected |
| `name` | Human-readable intent |
| `action` | `BLOCK`, `LOG`, or schema-level `ALLOW`; Phase 16 policy prohibits broad `ALLOW` |
| `condition` | Declarative `field:value` condition using an allowlisted field |
| `priority` | Bounded ordering value |
| `version` | Immutable rule version |
| `status` | `ENABLED`, `DISABLED`, `REMOVED` |
| `source` | Allowlisted provenance: CAP, Incident or Assessment |
| `checksum` | SHA-256 over canonical semantic content |

The checksum excludes operational status so a disable/remove transition retains the identity of the semantic rule. Enabled rules cannot be silently overwritten with different content; a governed rollback or new plan is required.

## 3. Lifecycle

```text
Response Plan
  -> policy and approval
  -> WAFResponsePlugin.plan
  -> immutable scope validation
  -> WAFResponsePlugin.validate
  -> WAFAdapter.apply
  -> MockWAFProvider state
  -> WAFAdapter.verify_applied
  -> WAF evidence receipt
  -> Response audit event
```

Rollback follows the existing Runtime path and permits only:

- `REMOVE`: transition the stored rule to `REMOVED`;
- `DISABLE`: transition the stored rule to `DISABLED`;
- `RESTORE`: restore the exact original validated rule and checksum.

The rollback token binds Plan ID, Incident ID, rule ID, version and checksum. It is stored privately by the Response Framework and excluded from API results.

## 4. Official/GitHub reference analysis

### 4.1 OWASP ModSecurity

ModSecurity models controls as SecLang rules with operators and ordered actions such as logging, deny/disruptive handling, phase and status. OWASP CRS commonly accumulates anomaly scores before a later blocking decision, which separates observations from disruptive enforcement. CAP adopts rule/action separation, but does not expose raw SecLang or arbitrary action chains in Phase 16.

Reference: <https://github.com/owasp-modsecurity/ModSecurity/wiki/Reference-Manual-(v3.x)>

### 4.2 OWASP Coraza

Coraza is a WAF engine/library compatible with SecLang and OWASP CRS. Integrations create a WAF instance, then a transaction, process connection/request phases, inspect interruption decisions and finally process logging/close the transaction. CAP mirrors the engine/integration separation through Provider/Adapter/Plugin, but the Phase 16 provider is synthetic.

Reference: <https://www.coraza.io/docs/tutorials/introduction/>

### 4.3 NGINX App Protect / F5 WAF for NGINX

F5 WAF for NGINX uses declarative security policies with attack signatures, violations, user-defined URLs/parameters, IP allow/deny lists and override rules. Policy files and update/reload mechanisms make policy deployment distinct from request inspection. CAP similarly stores neutral rule intent and delegates translation to an adapter; no F5/NGINX API is invoked.

Reference: <https://docs.nginx.com/waf/policies/configuration/>

### 4.4 Open Policy Agent

OPA separates policy evaluation from application enforcement: structured input and data are evaluated into a decision, while the integrating service decides how to apply it. CAP applies the same separation: `WAFPolicyProvider` produces a fail-closed authorization boundary, while `WAFAdapter` performs the provider operation only after approval and policy validation.

Reference: <https://www.openpolicyagent.org/docs/latest/policy-language/>

### 4.5 Kubernetes Gateway API / HTTPRoute

Gateway API models desired routing state declaratively through resources such as Gateway and HTTPRoute, while controllers publish observed status/conditions. CAP uses the same desired/observed split: a `WAFRule` is desired state, and Provider read-back plus `ResponseVerification` is observed state. CAP does not create Kubernetes resources in Phase 16.

Reference: <https://gateway-api.sigs.k8s.io/api-types/httproute/>

## 5. WAF rule safety analysis

### Avoiding business impact

- Mock-only provider: there is no real traffic path to affect.
- Allowlisted condition fields prevent arbitrary expressions and configuration injection.
- Newline, NUL, template, shell-like and statement delimiter syntax is rejected.
- Broad `ALLOW` rules are forbidden because they can bypass protections.
- Provider-owned rule IDs cannot be modified.
- An enabled rule cannot be replaced by different semantics without rollback.
- Asset IDs remain immutable and are included in result metadata as impact scope.

### Verification

Verification reads the provider state after each operation. Apply requires full rule equality and `ENABLED`; rollback requires the exact `REMOVED`, `DISABLED`, or original restored state. A successful plugin result without verified observed state is rejected by the existing Response Runtime.

### Approval

`response.waf` is allowlisted in the Response policy but always appears in `approval_required_capabilities`. The plugin independently rejects any WAF Plan that is not approval-required. The existing distinct-approver rule and approval expiration apply unchanged.

### Rollback

Rollback uses the execution-issued, framework-protected token and an allowlisted rollback action. The token is never returned by the public API. Each rollback produces new Evidence and Audit records and cannot mutate Incident, Asset, Finding or Report data.

### Impact scope and evidence

Evidence includes operation, complete rule snapshot, checksum, provider reference, change/idempotency flag, timestamp, and the provider's `network_access=false` / `production_access=false` declarations. Result metadata includes affected Asset IDs. The framework stores Evidence lineage against the Plan and Execution/Rollback IDs and publishes existing Response Audit events.

## 6. Compatibility and future adapters

A future ModSecurity, Coraza, F5/NGINX or Kubernetes adapter may implement the same WAF adapter semantics, but production credentials, network policy, staged rollout, canary verification and provider-specific compensation must receive a separate Architect-approved phase. Phase 16 intentionally proves governance and lifecycle correctness only.
