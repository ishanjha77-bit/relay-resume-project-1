# Runbooks

The team's runbooks for the shop (gateway, orders, payments, inventory, their
Postgres and Redis, and the external payment provider). The runbooks MCP server
indexes every file here except this README on startup: one chunk per `##`
section, searchable by keywords and by meaning (see `mcp-servers/runbooks`).

Every runbook follows the same template, checked by the tests:

```
# <Title: the failure mode, in the words an alert or a log would use>

<Overview: what fails, where, and which alerts lead here.>

## Symptoms     what you see: alerts, log lines, metrics
## Diagnose     how to confirm it, and how to tell it from look-alikes
## Mitigate     what to do; every action needs a human's approval
## Related      other runbooks, as runbook:<file name>
```

Write symptoms in the exact words the telemetry uses (error messages, metric
names): keyword search matches them verbatim. Name the look-alikes and how to
tell them apart, since that's where investigations go wrong.
