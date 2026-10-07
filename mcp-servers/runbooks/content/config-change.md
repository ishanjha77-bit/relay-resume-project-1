# Bad configuration change

A configuration change is a deploy without new code: the image stays the same,
an environment variable changes, and the pods restart with it. It can break a
service outright (it can't start), or degrade it quietly (a limit or a timeout
that is too small).

## Symptoms

- Problems that start right after a rollout whose image didn't change but whose
  environment did.
- Typical failures, by setting:
  - A wrong host in `DB_URL`: new pods crash at startup with
    `UnknownHostException` or "Connection refused". The rollout stalls while the
    old pods keep serving, and PodCrashLooping or DeploymentReplicasUnavailable
    fires.
  - A wrong address for a service it calls (`INVENTORY_URL`, `PAYMENTS_URL`):
    the pods run, but every call to that dependency fails ("Connection refused",
    `UnknownHostException`). The dependency itself stays healthy and keeps
    serving its other callers.
  - A smaller pool (`SPRING_DATASOURCE_HIKARI_MAXIMUMPOOLSIZE`): pool
    exhaustion once traffic is high enough (runbook:db-connection-pool).
  - A shorter timeout (`UPSTREAM_TIMEOUT_MS` on the gateway): the gateway
    returns 504 "upstream request timed out" quickly, although the upstream
    services are healthy (runbook:gateway-upstream).

## Diagnose

1. Read the rollout history: the latest revision of each service, what changed
   (image or environment), and the change-cause.
2. Compare the old and new values of the changed setting in the deployment's
   configuration.
3. Match the symptom to the setting: is the failure what that setting would
   cause? For timeouts, compare the configured timeout with the upstream's
   actual latency.
4. Check that the change came before the symptoms.

## Mitigate

- Revert the setting (roll back the revision), then confirm the symptom clears.

## Related

runbook:bad-deploy, runbook:pod-crashloop, runbook:gateway-upstream,
runbook:db-connection-pool
