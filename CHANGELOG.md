# Changelog

All notable changes to Unbound are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.16.1] - 2026-11-13

### Added
- Introduced this changelog, backfilled from release history.

## [0.16.0] - 2026-11-13

### Added
- Admins can reset a member's two-factor authentication when they're locked
  out (lost authenticator **and** recovery codes). A **Reset 2FA** button
  appears on each 2FA-enabled user card, backed by `POST /users/{id}/reset-2fa`
  (admin-only); it clears the stored secret without requiring a code — the
  acting admin is the authority — and records an auth event. The member can
  then sign in with their password and re-enroll.

### Fixed
- Recovery codes no longer vanish the instant 2FA is enabled. Enabling flips
  the account to "2FA on" via `refresh()`, and the Security page checked that
  state before the recovery-codes screen — so the one-time codes unmounted
  before they could be saved. The recovery-codes screen now takes precedence
  until you confirm "I've saved them."

## [0.15.0] - 2026-11-13
- Per-account AudiobookShelf library scoping and account routing in the profile UI.

## [0.14.0] - 2026-11-09
- Adopted `audible` 0.13.1 (new 4.60 device profile fixes `CustomerThrottled`).

## [0.13.5] - 2026-10-21
- Register the device as "Unbound" and deregister it on unlink/delete/relink.

## [0.13.4] - 2026-10-21
- Hold off downloads for an hour after a `CustomerThrottled` denial.

## [0.13.3] - 2026-10-21
- Explain `CustomerThrottled` license denials and pace license requests.

## [0.13.2] - 2026-10-21
- Surface Audible license denial reasons and flag non-downloadable titles.

## [0.13.1] - 2026-10-21
- Pin the ruff rule set (new ruff defaults broke CI lint).

## [0.13.0] - 2026-10-21
- Flag titles missing from Audible and AudiobookShelf.

## [0.12.1] - 2026-07-14
- Case-insensitive usernames.

## [0.12.0] - 2026-07-07
- Credit-only Audible purchasing (Phase 2).

## [0.11.2] - 2026-07-07
- Releases now gate on CI passing.

## [0.11.1] - 2026-07-07
- Fixed the network recovery probe on freshly booted hosts.

## [0.11.0] - 2026-07-07
- Multi-user roles and per-account access (purchase Phase 1.5).

## [0.10.0] - 2026-07-07
- Audible store search and wishlist (purchase Phase 1).

## [0.9.0] - 2026-07-07
- Jobs pagination, network-down handling, and AudiobookShelf already-in-library skip.

## [0.8.0] - 2026-07-07
- Auto-logout after 2h of admin inactivity.

## [0.7.0] - 2026-07-07
- Single Docker image (FastAPI serves the SPA).

## [0.6.0] - 2026-07-06
- Version and GitHub link in the app footer; new-version notifications.

## [0.5.0] - 2026-07-06
- OIDC single sign-on, live download progress, Jobs page redesign, auto-release workflow.

## [0.4.0] - 2026-07-06
- Two-factor authentication (TOTP) for local login.

## [0.3.0] - 2026-07-05
- Login cover collage; public README with screenshots.

## [0.2.0] - 2026-07-05
- UI polish, tabbed settings, pagination, and CI.
