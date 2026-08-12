# Private asset restore

The public source repository intentionally excludes API credentials, project metadata, and generated media. Trusted collaborators can restore the current private runtime bundle from the private GitHub release.

## One-time setup

1. Ask the repository owner to grant your GitHub account access to `lyh-123283/video-drama-agent-assets`.
2. Install GitHub CLI and authenticate:

```powershell
gh auth login
```

3. Clone the public source repository:

```powershell
git clone https://github.com/lyh-123283/video-drama-agent.git
cd video-drama-agent
```

4. Restore the private `.env` and `output/` files:

```powershell
.\tools\restore_private_assets.ps1 -Force
```

The script downloads release `v2026.08.12-assets`, verifies SHA-256, and merges the bundle into the checkout. It never deletes files already present in `output/`.

5. Start the application with `start_api_server.bat` or `启动.bat`.

## Security

The private bundle contains live API credentials. Do not upload it to the public repository or share the release URL outside the team. Revoke private-repository access and rotate all provider keys if a collaborator leaves or the bundle is exposed.
