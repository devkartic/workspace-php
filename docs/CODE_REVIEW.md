# Workspace Code Review

Review date: 2026-10-08. Scope: all active workspace source and configuration, including the Makefile, both shell scripts, Docker Compose, the PHP image recipe, Nginx/PHP settings, starter endpoints, project/environment templates, regression tests, and developer documentation. Local credentials, database files, backups, generated artifacts, and IDE metadata are outside the source review.

The review used the current working tree, including changes already present before this review. Those changes and staged removals were preserved.

## Issues resolved in this review

| Finding | Impact | Resolution |
| --- | --- | --- |
| No first-run setup or prerequisite check | Developers discover missing tools or Docker access only after a workflow fails | Added `make setup` and `make doctor`; setup preserves local configuration |
| Web root always equals the application source directory | Frameworks with `public/index.php` need different routing; source files may be exposed | Added validated `DOCUMENT_ROOT`, defaulting to `.` for compatibility; maps both Nginx and PHP paths |
| Line breaks can pass line-based field validation | Malformed values can reach generated configuration | Reject line breaks in names, hostnames, and document-root values |
| Duplicate hostnames are accepted | Multiple projects can compete for the same Nginx hostname | Reject duplicate hostnames before replacing generated configuration |
| Nginx upload limit does not match PHP defaults | Requests can return HTTP 413 well below the configured PHP limit | Set shared `client_max_body_size` to `128m` |
| Shared PHP settings lack an explicit log destination | Error-log behavior depends on runtime defaults or project overrides | Enable error logging to container stderr |
| Documentation mixes general setup with workstation-specific migration history | Fresh clones appear to depend on local backups or previous database state | Rewrote README and guide around reproducible setup, configuration, and recovery |
| Dependency directories lack workspace ignore rules | Application dependencies can accidentally enter version control | Ignore project `vendor/` and `node_modules/`; add EditorConfig |

## Existing behavior confirmed

- Each registered project selects its own PHP version and optional ini mount.
- Registration is separate from application `.env` files, and existing code/settings are preserved.
- Definitions and Compose configuration validate before generated files are replaced.
- Failed registration removes the new definition and preserves prior generated configuration.
- Removed registrations remove stale generated Nginx configuration.
- Nginx denies hidden paths and `project.env`, resolves PHP service DNS at request time, and returns 404 for unknown hosts.
- Database initialization uses `CREATE DATABASE IF NOT EXISTS` and runs against a healthy MariaDB service.
- The MariaDB shell reads credentials from the container rather than a host-side password variable.

## Remaining constraints

| Area | Constraint and development implication |
| --- | --- |
| Configuration trust | `WORKSPACE_*` settings are read from `.env.example` without executing application dotenv content. Make variables are also intended for trusted developer input. |
| Shared services | Projects share MariaDB root access and Redis. Separate database names do not enforce access isolation. Configure application cache prefixes and add scoped database users when required. |
| Network exposure | Compose port mappings currently use Docker's default host binding, which can expose them on interfaces beyond loopback. For an exclusively local environment, prefix mappings with `127.0.0.1:` in Compose. |
| Web root | The compatible default `DOCUMENT_ROOT=.` serves non-hidden files from the source root. Use the application's public directory to keep private source outside the web root. |
| Framework setup | The workspace does not install an application, load plain-PHP dotenv files, run migrations, build frontend assets, or manage workers/schedulers automatically. Follow the application's setup instructions. |
| Redis client | The PHP image does not install the Redis extension. Applications need a compatible client library or an image extension change. |
| Image reproducibility | Image tags are not pinned to digests; a future fresh build can resolve newer image contents. Verify application compatibility when rebuilding. |
| Generator concurrency | Configuration promotion spans multiple files and has no workspace lock. Run project registration/sync commands sequentially. |
| Recovery | Changing an environment password does not alter an initialized MariaDB account. Changing the Compose project name selects a different named volume. Preserve logical backups before storage changes. |
| Verification | Compose validation does not validate Nginx syntax or application behavior. The isolated routing check below covers the new document-root behavior; application integration tests remain project-specific. |

## Verification performed

- `make test`: **12 regression tests passed**, including new document-root generation, invalid paths and multiline fields, duplicate-host rejection, setup preservation, and unavailable-daemon checks.
- `make validate`: generated configuration passed Docker Compose validation.
- `sh -n scripts/projects scripts/workspace`: both scripts passed shell syntax checks.
- `php -l` on both starter endpoints: passed.
- `git diff --check`: passed.
- An isolated smoke check using locally available Nginx and PHP 8.4 images passed Nginx syntax validation and verified PHP routing, the actual PHP document root/script path, static files, hidden-file blocking, exclusion of private source from the web root, and unknown-host 404 responses. Its temporary containers and network were removed; no host ports were published.

The isolated runtime check did not rebuild every supported PHP image, exercise database migrations, or test a specific framework. The database stores and existing project containers were not changed by that check. Shared image-setting changes take effect when the relevant projects are rebuilt through the documented commands.
