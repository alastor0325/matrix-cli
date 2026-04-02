# Rules

## TDD (strictly enforced)
Write the failing test first. Do not write or modify any code in `matrix-cli`
until a test that exercises the new behavior exists and is confirmed to fail.
Two test suites are required:

- Unit tests (`tests/unit/`): always required, mock all I/O and network calls
- Integration tests (`tests/integration/`): required for any change that touches
  the Matrix API or session file I/O; run against the real Matrix room

Steps for every change:
1. Write the unit test — confirm it fails
2. Implement until it passes
3. If API/IO is involved, write the integration test — confirm it passes against the real room
4. Do not commit if any test in either suite is failing

## Testing
- Unit tests must pass on any machine with no `config/` present
- Integration tests are skipped automatically when `config/config` is absent

## README
If a change affects user-facing behavior, CLI flags, or the setup flow, update README.md.
