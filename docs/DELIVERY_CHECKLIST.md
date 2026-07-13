# Delivery checklist

This checklist is the release gate for a distributable DualCode Workbench build. A release is
not considered ready because it compiles; every applicable item below must have recorded evidence.

## Automated gate

Run the offline verification suite from the repository root:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools\verify_release.ps1
```

For a release candidate, also build both native artifacts:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools\verify_release.ps1 `
  -BuildSidecar -BuildDesktop
```

To produce and validate an installable MSI or NSIS release candidate, use:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools\verify_release.ps1 `
  -BuildSidecar -BuildInstaller
```

The command must finish successfully. It covers backend unit/API integration tests, desktop type
checking and unit tests, patch whitespace checks, and optionally the sidecar and Tauri release build.
The release-layout gate also verifies that all four version declarations agree, the Tauri sidecar
and runtime mappings are intact, native files use the Windows target-triple naming required by
Tauri, and requested EXE/installer artifacts exist and are non-empty.

## Release evidence

- [ ] Record the Git commit, application version, Windows version, and verification timestamp.
- [ ] Archive the verification output with the release candidate.
- [ ] Confirm the worktree contains no unintended or credential-bearing files.
- [ ] Confirm no `.env`, private key, access token, or user database is packaged.
- [ ] Confirm the sidecar and desktop executable versions come from the same commit.

## Clean-machine acceptance

- [ ] Install without requiring a developer toolchain or repository checkout.
- [ ] Launch with no existing application data; no demo workspace or task is created.
- [ ] Verify the backend health indicator reaches ready state and reports actionable failures.
- [ ] Add a local Git repository and complete two Codex turns in the same task.
- [ ] Cancel an active turn and start a new turn without restarting the application.
- [ ] Attach each supported image format and reject invalid or oversized attachments clearly.
- [ ] Exercise a sensitive Git action: reject once, approve once, and verify its audit history.
- [ ] Disconnect/reconnect the UI while a task runs and confirm persisted history is consistent.
- [ ] Restart during an active or approval-waiting task and verify the documented recovery behavior.
- [ ] Configure Claude SSH using a known-hosts entry; verify multi-turn and image behavior.
- [ ] Run a configured test command and inspect output, result status, Diff, and audit panels.
- [ ] Verify paths containing spaces and non-ASCII characters.

## Failure and recovery

- [ ] Missing Codex/Claude executables produce setup guidance, not a generic terminal failure.
- [ ] Invalid SSH host, key, known-hosts, and remote repository settings remain distinguishable.
- [ ] A crashed agent process transitions to a terminal state and permits retry.
- [ ] Backend restart, database migration, and incompatible/corrupt database behavior are documented.
- [ ] Logs can be located and exported without exposing credentials.
- [ ] Failed push/pull never performs an implicit merge or discards local changes.

## Installer lifecycle

- [ ] Validate first install, application restart, upgrade over the previous supported version, and
  uninstall on a clean Windows user profile.
- [ ] User data survives upgrade; uninstall behavior is explicit and confirmed.
- [ ] Verify code signing and installer provenance for public distribution.
- [ ] Confirm bundled WebView/runtime prerequisites and offline failure messaging.

## Release decision

Any unchecked security, data-integrity, restart-recovery, or installer item blocks a production
release. Product/UX exceptions must have an owner, severity, workaround, and target version.
