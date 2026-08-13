# Phase 19 EDR Safety, Drift and Security Boundary Case

## Security boundary

```text
CAP Response Plan + Approval + immutable Incident/Asset scope
                         |
                Worker/Sandbox Runtime
                         |
                 EDRResponsePlugin
                         |
           parse/authorize/verify/rollback
                    EDRAdapter
                         |
         exclusive Provider connection boundary
                  MockEDRProvider
                         |
             in-memory synthetic state only
```

The Plugin cannot directly access an Endpoint because its context contains only immutable identifiers, actor, typed parameters, rollback token and certified permissions. It receives no database session, Incident/Asset service, secret value, network client, filesystem writer or shell. Keeping endpoint connectivity exclusively in a Provider makes authentication, tenant/region selection, timeout/retry, rate limiting, vendor error mapping and read-back independently replaceable and auditable. It also prevents lifecycle code from bypassing policy or expanding scope.

Approval must exist because isolation can interrupt business services and management paths, while unisolation can restore attacker reachability. CAP enforces Response Plan approval and distinct requester/approver; the Plugin and Adapter independently reject non-approved execution. The Provider never decides approval.

## Safety cases

| Hazard | Prevention | Detection | Recovery |
|---|---|---|---|
| Wrong host isolated | Host UUID must exactly equal the single immutable HOST Asset; checksum and provider action ID bind content | Read-back includes host ID, last action ID and observed state | Stop on mismatch; approved unisolate only after identity validation |
| Wrong host unisolated | Only inverse action and validated rollback token; rollback target cannot change | Independent rollback read-back and Evidence | Re-triage; create a new approved isolate Plan if risk remains |
| Duplicate execution | Stable action ID + canonical checksum; same ID/different checksum rejected | Provider receipt marks idempotent replay | Return prior result; no second state transition |
| Host missing | Provider inventory lookup fails closed | `present=false`, state `UNKNOWN` | Investigate inventory/asset drift; no assumed success |
| Agent offline | Provider refuses action when `online=false` | Verification requires online Agent | Use alternate containment/runbook; retry only after confirmed recovery |
| Action accepted but not applied | Acceptance is never treated as success | Desired/observed read-back plus last action ID | Mark failed; preserve Evidence; operator escalation |
| Management lockout | Mock has no real network; production gate requires Provider-cloud/OOB reachability analysis | Post-action management reachability checks in future Provider | Out-of-band unisolate/emergency Provider console |
| Repeated rollback | Rollback action has its own stable ID and checksum | Idempotent Provider receipt + read-back | Return prior verified result |

## Configuration drift

```text
Response Plan HostAction = Desired State
              |
         Provider action
              |
       Provider read-back = Observed State
              |
      Evidence(desired, observed, checksum, action ID)
              |
       mismatch => Drift=true
              |
       Incident Candidate metadata
```

Drift includes wrong isolation state, missing host, offline Agent or action identity mismatch. The Plugin reports `drift_detected` and `incident_candidate` in verification/Evidence. It deliberately does not call Incident APIs and never auto-remediates. Incident creation/correlation remains a Platform Plane responsibility in a future reviewed workflow.

## Why no database migration

`HostAction` is the typed value inside existing `ResponsePlan.parameters` / plan JSON. Existing Response executions, rollback records, Evidence and Audit tables already carry execution state and proof. An EDR-specific table would duplicate Response Framework ownership, couple CAP to a vendor domain and create a bypass path; Phase 19 therefore has zero migrations and no ER change.
