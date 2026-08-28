# Fixtures

Drop saved example emails here (as JSON with `subject` / `sender` / `snippet` / `body`)
to extend offline tests. `test_pipeline.py` currently builds its own in-memory examples,
but real saved emails are useful for regression-testing tricky cases you flag in production.

Example shape:

```json
{"subject": "Interview invitation — Backend Engineer",
 "sender": "Talent <talent@stripe.com>",
 "snippet": "We'd love to schedule a first-round interview...",
 "body": "Hi, thanks for applying. We'd like to invite you to a 45-minute call..."}
```
