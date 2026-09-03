---
description: Run the pytest suite inside the Docker api container
---

Run the test suite in Docker and report results.

1. If the stack is already up, run: `docker compose exec api pytest $ARGUMENTS`
2. If it is not up, run: `docker compose run --rm api pytest $ARGUMENTS`
3. Summarize pass/fail counts. If anything fails, show the failing output and
   diagnose the cause — do not adjust a test or weaken an invariant to make it
   pass.
