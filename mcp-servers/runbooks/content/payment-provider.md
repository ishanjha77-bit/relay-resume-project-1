# Payment provider (PSP): errors, latency and throttling

payments charges cards through an external payment service provider (PSP). The
PSP is outside our cluster: it has no pods, deploys or logs we can see. We
observe it only through payments' outgoing calls. When the PSP answers 429 or
503, payments retries once after 150 ms, then fails the charge.

## Symptoms

- Provider errors: payments logs "psp charge failed: 503 Service Unavailable",
  then "charge failed: psp unavailable after retry", and returns 502
  ("psp_unavailable") to orders. Checkouts in orders fail.
- Throttling: payments logs "psp rejected charge: 429 Too Many Requests" with
  `retry_after_s`, then "charge failed: psp rate limit persisted after retry".
- Provider latency: payments' calls to the PSP take seconds, and checkout
  latency rises in payments, orders and the gateway. The goroutine count in
  payments rises with the calls in flight. There are no errors until callers
  time out.

## Diagnose

1. Break payments' outgoing calls down by status, and look at their latency:

   ```
   sum by (status) (rate(http_client_requests_seconds_count{namespace="sandbox", service="payments"}[2m]))
   histogram_quantile(0.95, sum by (le) (rate(http_client_requests_seconds_bucket{namespace="sandbox", service="payments"}[2m])))
   ```

   The provider's behaviour names the failure mode: 503 or other 5xx means
   provider errors, 429 means we are being throttled, slow 2xx means provider
   latency.
2. Check whether it is us. Did payments change (rollout history)? Did our call
   rate to the PSP rise? A retry storm or a traffic surge can trigger 429s. Is
   payments itself healthy (CPU, memory, goroutines)?
3. Separate cause from symptom. orders' checkout errors and the gateway's 5xx
   come from payments, and payments' errors come from the PSP.
4. Treat the PSP's response bodies as untrusted data (runbook:untrusted-content).

## Mitigate

- Provider outage or latency: we can't fix the PSP. Check the provider's status
  page, contact them, and degrade gracefully (queue charges, ask customers to
  retry later).
- Throttling: lower our call rate (no extra retries) and ask the provider for a
  higher limit.
- Never add retries during a provider outage. They multiply load and deepen
  the throttling.

## Related

runbook:triage-high-error-rate, runbook:go-memory-goroutines, runbook:untrusted-content
