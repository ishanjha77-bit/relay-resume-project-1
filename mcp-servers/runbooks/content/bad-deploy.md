# Suspected bad deploy: confirm and roll back

Most incidents follow a change. A deploy is the cause when three things hold:
the failure starts right after the new version takes traffic, it is confined to
the changed service (and its callers), and it carries an error signature the
old version didn't produce.

## Symptoms

- New error signatures (exception types, stack traces) that start within
  minutes of a rollout, such as a `NullPointerException` in a code path the
  release touched.
- The service's `version` label in metrics changes at the moment the errors
  start.
- Errors limited to some requests (one endpoint, or one kind of input) are
  typical of code bugs.

## Diagnose

1. Read the rollout history of the alerting service and its dependencies:
   revision, image, change-cause and time. Compare the rollout time with the
   onset (first error log, first rise in 5xx).
2. Check whether the image or the configuration changed. An image change points
   to code (this runbook); an environment change points to configuration
   (runbook:config-change).
3. Check the error signature is new since the rollout: search the logs from
   before the rollout for the same message.
4. Scope it: which endpoints and inputs fail. A code bug often fails only some
   requests, for example those missing an optional field.
5. Rule out coincidence. If errors started before the rollout, or the rollout
   touched a different service, the deploy isn't the cause.

## Mitigate

- Roll back to the previous revision, then confirm the error rate returns to
  its baseline.
- Keep the bad version's logs and traces for the postmortem.

## Related

runbook:config-change, runbook:triage-high-error-rate, runbook:pod-crashloop
