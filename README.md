# SurAdda Backend

FastAPI backend for **SurAdda** (*sur* + *adda* — melody + casual gathering), a social music streaming app built for Bangladeshi users as part of the BdApps National Android Development Bootcamp.

## Tech Stack

- **Framework:** FastAPI (async)
- **Database:** PostgreSQL (hosted on Supabase), accessed via async SQLAlchemy 2.0 + Alembic migrations
- **Media storage:** Cloudinary CDN
- **Auth:** JWT (access + sliding refresh tokens), with phone-number–based identity
- **Real-time:** Native WebSockets for synchronized playback
- **Hosting:** Render
- **Carrier billing / OTP:** bdapps TAP API, proxied through a PHP layer on cPanel (for the stable, whitelistable IP bdapps requires)

## Features

### Authentication
- JWT-based session management with short-lived access tokens and a sliding refresh window
- Phone number (E.164-validated) as the required, unique identity field
- Designed to migrate cleanly to bdapps OTP-based auth — session issuance is fully decoupled from the verification method, so swapping password auth for OTP requires no changes to token logic, dependencies, or downstream authorization

### Music Library
- Song catalogue stored in PostgreSQL, media files served from Cloudinary
- Search by title or artist
- Songs are UUID-keyed and foreign-keyed cleanly into room state (`rooms.current_song_id`)

### Admin Catalogue Management
- Separate `/admin` route group, gated by a constant-time-compared shared key (`X-Admin-Key`) plus an optional IP allowlist — deliberately not JWT-gated, since the user table has no roles and any subscriber holding a valid token must not be able to manage the catalogue
- Upload, edit, and delete tracks; deleting a song safely clears any room currently pointing at it before removal
- Mounted outside the versioned `/api/v1` prefix, since it is an internal tool rather than part of the public app contract

### Online Party Rooms
- Host-authoritative synchronized playback over the internet
- Rooms identified by short, shareable alphanumeric codes — no friend graph or contact lookup required to start listening together
- A single WebSocket connection per room per client carries all control messages (`play`, `pause`, `seek`, `sync_position`), broadcast server-side to every other connected participant
- Periodic host-side position pings correct for playback drift between devices
- Live participant list (with usernames) broadcast on every join and leave

### Subscription & Carrier Billing (bdapps integration)
- `is_subscribed` status enforced at the API layer — gated endpoints (song access, room creation/joining, WebSocket connections) require an active subscription, while `/auth/me` stays reachable so the client can always determine its own state
- Subscription status is periodically re-validated against bdapps during token refresh, rather than trusted indefinitely from a single login event
- A dedicated internal endpoint (`/internal/issue-token`), authenticated by a shared secret rather than a public credential, allows the trusted PHP/bdapps layer to request a session on a verified user's behalf — Flutter never talks to bdapps or holds bdapps credentials directly

### Infrastructure Notes
- All timestamps and IDs use native PostgreSQL types (`UUID`, `TIMESTAMPTZ`) for consistency across every table
- Alembic manages all schema changes; no manual DDL against the production database
- Deployed via Docker to Render, with environment-based configuration (`.env`) for all secrets and connection strings — nothing sensitive is hardcoded

## API Structure

```
/api/v1/auth/...        JWT issuance, refresh, current-user lookup
/api/v1/songs/...       Cloud catalogue browsing
/api/v1/rooms/...       Party creation, joining, state
/api/v1/ws/rooms/{id}   WebSocket control channel for a specific party
/admin/songs/...        Catalogue management (shared-key gated, unversioned)
/internal/...           Server-to-server endpoints (shared-secret gated)
/health                 Liveness check
```

## Local Development

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload
```

Environment variables required (see `.env.example` if present, or `app/core/config.py` for the full list): database URL, JWT secret, Cloudinary credentials, admin key, internal shared secret, bdapps/PHP proxy configuration.
