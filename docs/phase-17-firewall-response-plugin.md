# Phase 17 Firewall Response Plugin Architecture and Safety Case

## 1. Purpose and boundary

Phase 17 adds only `response.firewall` through the existing Response SDK. The Response Framework, database schema and public API remain unchanged. The provider is synthetic and enforces `network_access=false` and `production_access=false`.

## 2. Architecture

```text
Response Runtime (unchanged)
          |
FirewallResponsePlugin
          |
FirewallAdapter
       /      \
FirewallPolicy  MockFirewallProvider
          |
      FirewallRule
```

The Plugin owns lifecycle translation, the Adapter owns parameter parsing and provider-neutral operations, Policy owns authorization and safety, and Provider owns observed state. No Plugin or Provider can access CAP repositories or mutate Incident/Asset.

## 3. Official and GitHub reference analysis

### 3.1 nftables

nftables organizes policy as `Table -> Chain -> Rule`. Base chains attach to Netfilter hooks, use priority and chain policy, while rules contain match expressions and verdict statements. Rule handles identify observed rules and ruleset replacement can be atomic. CAP adopts table, chain, priority, desired rule and read-back verification, but does not expose raw nft syntax.

References: nftables wiki and Netfilter nft man page; upstream projects under `netfilter/nftables`.

### 3.2 iptables

iptables tables contain built-in and user-defined chains. A rule is ordered match criteria plus a Target such as ACCEPT, DROP, RETURN or another chain. Built-in chains have a default Policy. CAP maps Target to `FirewallAction`, chain to direction-scoped placement, and preserves explicit priority/order while rejecting default-policy changes in Phase 17.

References: iptables(8), Netfilter project and upstream `git.netfilter.org/iptables`.

### 3.3 pfSense

pfSense rules pass or block traffic between networks and use Aliases to group addresses. Interface, direction, source, destination, protocol and ports form policy scope. CAP keeps explicit CIDRs rather than resolving aliases in the core model; a future pfSense Adapter may translate governed aliases but must report resolved members as Evidence.

References: Netgate pfSense Firewall and Aliases documentation; `pfsense/pfsense`.

### 3.4 OPNsense

OPNsense uses stateful rules grouped by interface. Actions include Pass, Block and Reject. Floating, group and interface sections have processing order; quick rules are first-match, and sort order/sequence affects behavior. State tables may preserve existing flows after a policy change. CAP therefore requires priority, observed-state verification and a future provider-specific state-impact report; Phase 17 performs no live state reset.

References: OPNsense Firewall Rules documentation; `opnsense/core` and `opnsense/docs`.

### 3.5 Open Policy Agent

OPA separates policy decision from enforcement. Structured input and data are evaluated to produce a structured decision, with explicit defaults enabling fail-closed behavior. CAP adopts this separation: `FirewallPolicyProvider` decides whether a rule is allowed, while `FirewallAdapter/Provider` enforce and observe it. Phase 17 does not execute Rego or connect to OPA.

References: Open Policy Agent official documentation; `open-policy-agent/opa`.

## 4. Provider-neutral rule model

`FirewallRule` includes:

- identity: `id`, `name`, `version`;
- decision: `action` (`BLOCK`, `REJECT`, `LOG`);
- placement: `direction`, `table`, `chain`, `priority`;
- match: source/destination CIDR, protocol and ports;
- lifecycle: `status`;
- governance: explicit `impact_scope` and canonical SHA-256 checksum.

Only the `filter` table is allowed. Ingress maps to INPUT, egress to OUTPUT and forward to FORWARD. Raw commands, NAT, default chain policies, arbitrary targets and dynamic scripts are outside Phase 17.

## 5. Safety case

### 5.1 Why it cannot accidentally block production

The Mock Provider has no network client, credentials, subprocess, shell or filesystem path. No real firewall is reachable. At the model/policy level the implementation rejects any/default-route scopes, broad prefixes, management/control-plane networks, protected management ports, any-protocol deny rules, provider-owned rules and enabled-rule semantic replacement.

### 5.2 Rule verification

Apply writes only synthetic app-local state. Verification reads the rule back and requires complete equality plus `ENABLED` status. Evidence records desired and observed rule identity, checksum, provider reference and zero-access flags. The Response Runtime rejects a successful but unverified result.

### 5.3 Rollback

Rollback is limited to `REMOVE`, `DISABLE` or `RESTORE`. A private token binds Response Plan ID, Incident ID, rule ID, version and checksum. Each rollback is read-back verified and persisted as new Evidence and Audit activity.

### 5.4 Avoiding management-network lockout

The policy protects configured management CIDRs, loopback, link-local and multicast networks and management ports 22, 3389, 443 and 8443. Direction must match chain placement and impact scope must exactly equal immutable Response Plan Asset scope. A future production provider must additionally prove out-of-band management reachability before commit.

### 5.5 Limiting blast radius

CIDRs must be explicit and bounded; IPv4 prefixes broader than /8 and IPv6 prefixes broader than /32 are rejected. Port list size and priority are bounded. Default policy changes, table flush, chain deletion, NAT and unrestricted ANY-protocol deny are not represented. All changes require approval.

## 6. Compatibility and future provider boundary

A production Adapter must translate the canonical model into provider syntax without extending Plugin permissions. It must add provider authentication isolation, staged/canary deployment, atomic commit or compensation, current-ruleset version checks, state-table impact analysis, out-of-band reachability probes, reconciliation, distributed locking and emergency bypass. Those capabilities require a future Architect-approved phase and ADR.
