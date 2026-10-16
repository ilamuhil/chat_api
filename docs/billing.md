# Billing integration

The authoritative catalogue, migrations, customer flow and configuration are documented in `chat-dashboard/docs/billing.md`. This service reads the dashboard billing tables and owns durable AI/training reservations, usage finalization, visitor fallback and retention jobs.

- Configure `DASHBOARD_APP_URL` and `BILLING_MAINTENANCE_SECRET` for the existing RQ scheduler. Docker development uses `http://host.docker.internal:4000`.
- `BILLING_STANDARD_MODEL` selects the bounded standard model; the default and versioned cost rates are in `app/services/billing.py`. Unknown model rates remain explicitly unpriced. Review rates before changing the model.
- `APP_ENV_FILE` optionally selects an isolated local environment file for verification without changing Docker configuration.
- `python -m unittest discover -s tests` runs the regression suite. `RUN_BILLING_DB_TESTS=1 python -m unittest discover -s tests -p test_billing.py` additionally exercises development database concurrency, retry accounting, training, expiry and retention with synthetic records.
- `python -m app.scripts.test_billing_flow` performs one real bounded OpenAI turn, checks zero-balance contact/handoff and exports messages, then cleans its temporary records. It requires sandbox fixtures from the dashboard tests. Set `BILLING_TEST_FIXTURES`, `BILLING_TEST_BASE_URL`, `BILLING_TEST_WS_URL` and `BILLING_TEST_OUTPUT` as needed. Defaults target dashboard 4000 and chat API 18000. This opt-in test consumes a small amount of actual OpenAI usage.
- The developer Docker API has a five-second graceful shutdown timeout so long-lived notification streams cannot indefinitely block code reloads. Redis, workers and scheduler remain separate services.

AI exhaustion does not block lead capture or a direct counsellor request. The visitor widget hides quota/billing explanations; staff can review usage in the dashboard.
