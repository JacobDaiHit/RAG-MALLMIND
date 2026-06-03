# Codex Auto Review Timeout Remediation

Last checked: 2026-05-27

## Finding

The recurring "automatic permission approval review did not finish before its deadline" issue is not caused by this project.
It comes from the Codex Desktop execution environment when auto-review is asked to approve escalated shell commands.

Observed evidence:

- The project is already trusted in `C:\Users\Jacob\.codex\config.toml`.
- The local config uses `[windows] sandbox = "elevated"`.
- Recent logs contain repeated sandbox command failures:
  `windows sandbox: runner error: CreateProcessAsUserW failed: 5`.
- Recent logs contain repeated auto-review transport failures:
  `model=codex-auto-review`, `stream disconnected - retrying sampling request`, and
  `startup websocket prewarm timed out before the first turn could use it`.
- `pwsh.exe` resolves first to Microsoft Store / WindowsApps paths:
  `C:\Program Files\WindowsApps\Microsoft.PowerShell_7.6.1.0_x64__8wekyb3d8bbwe\pwsh.exe`
  and `C:\Users\Jacob\AppData\Local\Microsoft\WindowsApps\pwsh.exe`.
- `rg.exe` also resolves to the Codex WindowsApps bundle:
  `C:\Program Files\WindowsApps\OpenAI.Codex_...\app\resources\rg.exe`,
  and direct launch can return access denied.

In short: sandboxed process startup is brittle on this machine because important command executables are AppX/WindowsApps
targets. When sandbox startup fails, the agent asks for escalation; auto-review then starts a separate
`codex-auto-review` request, and that request can time out or disconnect. The two problems amplify each other.

## What I changed

2026-05-27 implementation:

- Backed up Codex config and the previous user PATH under:
  `C:\Users\Jacob\.codex\backups\auto-review-fix-20260527`.
- Created `C:\Users\Jacob\.codex\tools`.
- Added a normal `rg.exe` copied from the VS Code installation, not from the Codex AppX/WindowsApps bundle.
- Added `pwsh.exe` copied from `C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe`.
  This gives PATH lookup a real Win32 executable named `pwsh.exe` before WindowsApps.
- Added `pwsh.cmd` as an additional explicit wrapper to Windows PowerShell.
- Updated the user PATH so `C:\Users\Jacob\.codex\tools` is first and
  `C:\Users\Jacob\AppData\Local\Microsoft\WindowsApps` is last.

Not changed:

- Codex authentication files.
- Codex state or log SQLite databases.
- Windows registry beyond the normal user PATH environment value.
- AppX package files under `C:\Program Files\WindowsApps`.

## Recommended Fix Plan

1. Prefer the current full-access mode for this trusted local project while doing heavy code cleanup.

   This bypasses the fragile sandbox-plus-auto-review path and avoids repeated review dead time.

2. Replace Store/AppX command targets with normal Win32 executables.

   Recommended shell target:

   ```text
   C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe
   ```

   Current mitigation already provides:

   ```text
   C:\Users\Jacob\.codex\tools\pwsh.exe
   C:\Users\Jacob\.codex\tools\pwsh.cmd
   ```

   Long-term alternative: install PowerShell from the official MSI so it exists at:

   ```text
   C:\Program Files\PowerShell\7\pwsh.exe
   ```

   Then put that path before `C:\Program Files\WindowsApps` and
   `C:\Users\Jacob\AppData\Local\Microsoft\WindowsApps` in PATH, or configure Codex Desktop to use it if the UI exposes a shell path setting.

3. Install a normal ripgrep binary outside WindowsApps.

   Current mitigation already provides:

   ```text
   C:\Users\Jacob\.codex\tools\rg.exe
   ```

   This `rg.exe` was copied from VS Code's regular installation directory and verified with `rg --version`.

4. If auto-review mode must be used, reduce how often escalation is needed.

   Use narrowly scoped persistent rules for repeat-safe commands, for example:

   ```text
   ["python", "-m", "pytest"]
   ["git", "status"]
   ["git", "diff"]
   ```

   Avoid broad rules such as a whole-shell prefix. Avoid requesting approval for commands that contain shell metacharacters,
   here-strings, arbitrary scripts, or destructive filesystem operations.

5. Avoid large parallel shell batches while in sandbox mode.

   The logs show multiple simultaneous sandbox setup refreshes and read-ACL helpers. In sandbox mode, prefer one or two
   shell calls at a time for file inspection.

6. If the sandbox remains unstable, reset only the generated sandbox helper state.

   Sensitive step, do manually after closing Codex Desktop:

   ```text
   Rename C:\Users\Jacob\.codex\.sandbox to .sandbox.bak
   Rename C:\Users\Jacob\.codex\.sandbox-bin to .sandbox-bin.bak
   Restart Codex Desktop
   ```

   Do not delete `auth.json`, `config.toml`, `state_*.sqlite`, or `logs_*.sqlite`.

7. If auto-review still times out after shell/path cleanup, treat it as a Codex Desktop transport issue.

   The relevant log signature is:

   ```text
   model=codex-auto-review
   stream disconnected - retrying sampling request
   startup websocket prewarm timed out before the first turn could use it
   ```

   In that case, use manual approval or full access for trusted workspaces until the desktop app or network path is updated.

## Restart Required

The PATH update is written to the user environment and registry, but the currently running Codex Desktop process keeps
its old inherited environment. Restart Codex Desktop before expecting plain `rg` or `pwsh.exe` lookup to resolve through
`C:\Users\Jacob\.codex\tools`.

After restart, verify:

```text
where rg
where pwsh
rg --version
pwsh -NoProfile -Command "$PSVersionTable.PSVersion.ToString()"
```

Expected first hits:

```text
C:\Users\Jacob\.codex\tools\rg.exe
C:\Users\Jacob\.codex\tools\pwsh.exe
```

## Rollback

To roll back the environment change:

```text
Restore the user PATH from:
C:\Users\Jacob\.codex\backups\auto-review-fix-20260527\user-path.txt

Optionally remove:
C:\Users\Jacob\.codex\tools\rg.exe
C:\Users\Jacob\.codex\tools\pwsh.exe
C:\Users\Jacob\.codex\tools\pwsh.cmd
```

## Safe Operating Recommendation

For this repository, use full access for local refactors and tests, because the project path is trusted and all edits are
inside `D:\github\tripmind\trad_rag`. Use auto-review only when you intentionally want a second review layer for commands
that touch system locations, network installs, registry, global Codex config, or destructive filesystem operations.
