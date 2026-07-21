# Production monitoring contract

Scrape `http://api:8001/metrics` only from the private `backend` network. The
Caddy route intentionally does not expose `/metrics` to the public internet.

Load `prometheus-rules.yml` into the approved Prometheus-compatible monitoring
service and route `critical` alerts to the release/on-call channel before
releasing `OUTBOUND_HARD_PAUSE`. During the email canary, alerts for Provider
uncertainty, stale outbound/inbox workers, failed metrics collection, unhealthy
enabled accounts, bounce/unsubscribe thresholds, and any Provider complaint are
hard-stop signals. Complaint ingestion also creates a database-enforced sender
account SafetyLock, so alert delivery is not the only containment boundary.

The application metrics complement, rather than replace, managed MySQL health,
replication/backup alerts, Caddy availability/TLS alerts, container restart and
resource alerts, SMTP/IMAP provider dashboards, and complaint/bounce reporting.
