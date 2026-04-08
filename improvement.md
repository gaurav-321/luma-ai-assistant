# Codebase Improvement Review

## Most Odd / Inefficient Thing

Your memory retrieval mode is set to `"8h"` for normal chat traffic, but the memory filter does not support `"8h"`.

- Producer: `core/llm.py:214`
- Filter cases: `core/memory_helpers.py:74`, `core/memory_helpers.py:81`, `core/memory_helpers.py:88`, `core/memory_helpers.py:95`

Impact:
- silently falls back to unbounded time filter logic
- higher retrieval noise and bigger prompt payloads
- unnecessary latency and token spend

What I would change:
1. Add explicit support for `"8h"` in `_semantic_filter`.
2. Validate `mode` against an enum and fail fast on unknown values.

## Severity-Ranked Changes I Would Make

### Critical

1. Hardcoded credentials/passwords in source code.
- `core/utils/config.py:22`
- `core/utils/config.py:94`
- `core/utils/config.py:95`
- `core/telegram_bot.py:28`
- `core/telegram_bot.py:173`

Why this is severe:
- immediate secret leakage risk and account takeover surface

Change:
- remove defaults for secrets
- fail startup if required env vars are missing
- rotate leaked credentials
- restrict `/init_user` to allowlisted admin IDs

### High

1. Blocking sync I/O in async hot path.
- `core/memory_helpers.py:116` (`requests.post`)
- `core/memory_helpers.py:265` (`async` function wrapping sync DB/network calls)
- call sites in request path: `core/llm.py:225`, `core/llm.py:326`, `core/llm.py:387`

Why this is severe:
- event-loop stalls under load
- slow requests block unrelated requests

Change:
- move embedding/Qdrant calls to async clients or executor pool
- add timeout/retry/circuit-breaker policy

2. SQLite durability turned off in runtime paths.
- `core/workers/watcher.py:34`
- `core/workers/watcher.py:35`
- `core/telegram_bot.py:134`
- `core/telegram_bot.py:135`

Why this is severe:
- crash/power-loss can corrupt DB or drop writes

Change:
- use `WAL` mode + normal sync defaults unless this is disposable cache data

3. Per-message initialization work for vector schema checks.
- provider recreated each message: `core/llm.py:16`, `core/llm.py:224`
- collection setup path: `core/memory_helpers.py:61`, `core/memory_helpers.py:140`, `core/memory_helpers.py:162`

Why this is severe:
- repeated metadata/index checks in hot path
- avoidable overhead per request

Change:
- keep a long-lived provider/client singleton and run collection/index bootstrap once at startup

### Medium

1. Watcher user/module lists are static at import time.
- `core/workers/watcher.py:17`
- `core/workers/watcher.py:19`

Why this matters:
- new users/watchers are not picked up until process restart

Change:
- re-scan periodically or cache with TTL + invalidation

2. Cron next-run logic uses brute-force minute scanning.
- `core/workers/watcher.py:232`
- used in scheduler loop: `core/workers/watcher.py:397`, `core/workers/watcher.py:401`

Why this matters:
- scales poorly as watchers/users increase

Change:
- use a cron parser/iterator with direct next-fire computation

3. Watcher always enqueues LLM work even for low-value events.
- enqueue path: `core/workers/watcher.py:338`

Why this matters:
- extra queue pressure and inference spend

Change:
- gate enqueue on `action_required`/severity and dedupe repeated alerts

## Critic vs Planner (Argument)

### Critic
"You are paying inference + latency tax because runtime contracts are loose and hot paths are blocking. The biggest smell is hidden fallback behavior (`8h` unsupported), plus secrets in code and durability disabled."

### Planner
"Fix order should be: security first, then async hot-path unblocking, then data durability, then watcher/scheduler scaling. This gives immediate risk reduction and measurable latency gains in one week."

### Best Modal (Recommended)

Use a **contract-first, event-driven model**:
1. Strict typed contracts between planner, manager, watcher, and memory layers.
2. Non-blocking async execution in all hot paths.
3. Fail-soft behavior (memory/store outages should not block primary reply path).
4. Backpressure and gating on watcher-generated events.
