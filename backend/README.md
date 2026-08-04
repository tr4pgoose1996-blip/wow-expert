# wow! expert — backend

AI-powered World of Warcraft Game Master, guide, coach, and companion.

FastAPI service with a modular architecture, async PostgreSQL, Redis caching,
and a complete Blizzard Battle.net integration.

---

## Quick start

```bash
cp .env.example .env          # then fill in the Battle.net credentials
uv venv && uv pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload
```

Interactive API documentation is served at <http://localhost:8000/docs>.

With Docker, from the repository root:

```bash
docker compose up --build
```

---

## Layout

```
app/
├── api/                 HTTP layer: routing, dependencies, middleware
│   └── v1/endpoints/    auth, characters, blizzard, health
├── core/                config, logging, security, crypto, errors, redis
├── db/                  engine, session, ORM models
├── integrations/
│   └── blizzard/        Battle.net client, OAuth, parsers, constants
├── modules/             pluggable feature modules (registry-mounted)
├── repositories/        data access, one per aggregate
├── schemas/             Pydantic request/response contracts
└── services/            business logic
```

The layering rule is one-directional: endpoints call services, services call
repositories and integrations, repositories touch the database. Nothing calls
back up the stack, which is what keeps modules independently testable.

### Adding a feature module

Subclass `FeatureModule`, expose a router, and register it. It is mounted at
`/api/v1/modules/<name>` automatically, with no edits to the router or app
factory:

```python
from app.modules.registry import FeatureModule, registry

class MyModule(FeatureModule):
    name = "my-module"
    description = "What it does."

    @property
    def router(self) -> APIRouter: ...

registry.register(MyModule())
```

### Built-in modules

| Module | Mount | What it teaches / does |
| --- | --- | --- |
| `rotation` | `/api/v1/modules/rotation` | Real-time rotation advisor for all 40 specializations: priority system, cooldown planning, AoE/single-target/movement/execute logic, resource/talent/gear/trinket awareness. |
| `coach` | `/api/v1/modules/coach` | Combat Coach for every dungeon, raid, boss, and Mythic+: tank/healer/DPS mechanics, interrupts, movement, cooldown timing, burst windows, defensive planning, and common mistakes across Heroic/Mythic/Mythic+. |
| `gear` | `/api/v1/modules/gear` | Gear Advisor: simulate stats, compare upgrades, recommend enchants, gems, trinkets, crafted gear, and Great Vault choices for every class and spec. |
| `collections` | `/api/v1/modules/collections` | Collection Tracker: mounts, hunter/battle pets, toys, appearances, achievements, titles, tabards, druid forms — fastest-obtainable goals, completion ETAs, and class/spec filtering. |
| `reasoning` | `/api/v1/modules/reasoning` | **Game Master reasoning core.** Hermes infers player intent, decomposes it into ordered objectives sourced from every subsystem (rotation, gear, coach, collections), ranks them by an efficiency model, and explains *why* each step is optimal. Learns the player over time (`/profile`) and remembers conversations (`/conversations`). |
| `realtime` | `/api/v1/modules/realtime/ws/overlay/{user_id}` | **Transparent desktop overlay** WebSocket: streams the rotation display, cooldown tracker, quest tracker, boss alerts, rare-spawn alerts, and waypoints. Frames are personalized via the reasoning engine. The Electron overlay client lives in `frontend/overlay/`. |

Both are stateless feature modules. The rotation engine is a single generic
code path over a `RotationProfile` per spec; the coach generates role- and
difficulty-scaled briefings from structured encounter facts, so coverage
extends to new content without per-fight hand-authoring. Blizzard Journal data
(`/data/wow/journal-instance`, `/data/wow/journal-encounter`) is an optional
enrichment layer that fills in live names and descriptions when credentials
and network are available.

---

## Battle.net integration

Everything Blizzard-related lives under `/api/v1/blizzard`.

### Connecting an account

1. `GET /oauth/authorize` returns a Battle.net consent URL. The CSRF `state`
   is stored in Redis with a ten-minute TTL and is single-use.
2. The user approves, and Blizzard redirects to `/oauth/callback`.
3. The service exchanges the code, reads the Battle.net id and BattleTag from
   `/userinfo`, and links the account to the signed-in user.

Refresh tokens are encrypted with Fernet before they are written to the
database; no endpoint ever returns token material.

### Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/oauth/authorize` | Begin account linking |
| `GET` | `/oauth/callback` | OAuth redirect target |
| `GET` | `/accounts` | List linked accounts |
| `DELETE` | `/accounts` | Disconnect and delete tokens |
| `GET` | `/roster` | Preview the roster without importing |
| `POST` | `/import` | Import characters |
| `POST` | `/sync` | Synchronize the whole account |
| `POST` | `/characters/{id}/sync` | Synchronize one character |
| `GET` | `/characters/{id}/snapshot` | Read imported data |
| `GET` | `/collections` | Account-wide mounts and pets |
| `GET` | `/jobs` | Synchronization history |
| `GET` | `/realms` | Search realms |
| `GET` | `/realms/{slug}` | Read one realm |
| `GET` | `/guilds/{realm}/{name}` | Look up a guild |
| `GET` | `/guilds/{realm}/{name}/roster` | Read a guild roster |
| `GET` | `/status` | Integration configuration status |

### Import scopes

A sync is scope-driven, so callers import only what they need:

`profile`, `equipment`, `talents`, `professions`, `achievements`,
`reputations`, `mounts`, `pets` — or `full` for all of them.

```bash
curl -X POST localhost:8000/api/v1/blizzard/characters/$ID/sync \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"scopes": ["equipment", "talents"]}'
```

Scopes are fetched concurrently and reported individually. If Blizzard fails
on one domain the rest still import and the job is marked `partial`, so a
transient outage in one endpoint never costs the whole sync.

### Token refresh

Callers never handle expiry. `BlizzardTokenManager` returns a usable token,
refreshing it when it is within sixty seconds of expiring and persisting the
rotated value. When Blizzard reports `invalid_grant` — the user revoked
access — the account is deactivated with an explanatory `sync_error` rather
than retried forever.

Application tokens for public Game Data use a separate client-credentials
flow, cached in memory behind a lock so a burst of concurrent calls triggers
exactly one token request.

### Weekly synchronization

`SyncScheduler` runs in-process, waking on `SYNC_INTERVAL_SECONDS` and
claiming a Redis lock so only one replica sweeps. Accounts become eligible
once their last sync is older than `SYNC_STALE_AFTER_HOURS` (168 by default).
Because eligibility is per-account rather than a fixed weekday, load spreads
naturally instead of stampeding. Each account syncs in its own transaction,
so one failure cannot roll back another's work.

### Resilience

The API client applies, in order: automatic token attachment, retry with
exponential backoff and jitter on 5xx/429/timeouts, `Retry-After` support,
a concurrency semaphore, and Redis response caching. Static data (realms,
items) is cached for a day; profile data for fifteen minutes. Responses
fetched with a *user* token are never cached, since a shared cache key would
leak one player's data to another.

---

## Testing

```bash
pytest                                  # full suite
pytest --cov=app --cov-report=term-missing
```

The suite is hermetic — SQLite in-memory, a Redis stub, and mocked Blizzard
transports — so no services are required and no network calls are made.

| File | Covers |
| --- | --- |
| `test_blizzard_parsers.py` | JSON translation, including malformed payloads |
| `test_blizzard_oauth.py` | Token lifecycle, refresh, encryption |
| `test_blizzard_client.py` | Retry, backoff, caching, error mapping |
| `test_blizzard_sync.py` | Import and synchronization behaviour |
| `test_blizzard_api.py` | HTTP contracts, auth, tenant isolation |
| `test_rotation.py` | Rotation engine: priority, AoE/ST, movement, execute, cooldowns, talent/gear; API auth + validation |
| `test_coach.py` | Coach domain + generator (every role/difficulty/affix), service, API |
| `test_gear_collections.py` | Gear Advisor (sim/compare/enchant/gem/vault) + Collection Tracker (fastest goals, ETA, class filter) |

---

## Configuration

Every setting is an environment variable; see `.env.example` for the
annotated list. No secret has a usable default.

`SECRET_KEY` is required and must be at least 32 characters. It signs JWTs
and, unless `BLIZZARD_TOKEN_ENCRYPTION_KEY` is set, derives the key that
encrypts stored Battle.net tokens — set that separately if you want to rotate
JWT signing without invalidating every stored grant.

Register a client at <https://develop.battle.net/access/clients>. Its
redirect URI must match `BLIZZARD_REDIRECT_URI` exactly.

---

## Migrations

```bash
alembic upgrade head                        # apply
alembic revision --autogenerate -m "..."    # create
alembic downgrade -1                        # roll back one
```

CI applies every migration, rolls the whole chain back to base, re-applies
it, and runs `alembic check` to catch models that drifted from the schema
without a migration.

---

## Desktop overlay (Hermes, the Game Master)

A transparent, click-through desktop overlay that surfaces Hermes' advice
in real time while you play.

- **Backend:** the `realtime` module exposes a WebSocket at
  `/api/v1/modules/realtime/ws/overlay/{user_id}?token=...`. It streams
  rotation display, cooldown tracker, quest tracker, boss alerts, rare-spawn
  alerts, and waypoints — all personalized via the `reasoning` engine.
- **Client:** `frontend/overlay/` is an Electron + React + TypeScript (Vite)
  app. The Electron window is `transparent`, `frame: false`, and
  `setIgnoreMouseEvents(true, { forward: true })` so clicks pass through to the
  game; widgets opt back into pointer events locally. Set `OVERLAY_BACKEND_URL`
  to point the overlay at a backend on another host.

Run it:

```bash
cd frontend/overlay
npm install
npm run build            # type-check + production bundle (verified)
npm run electron:dev    # vite build + launch the overlay window
```

The overlay connects with `?userId=&token=` from the auth flow; without those
it uses demo values so the UI is exercisable end-to-end.
