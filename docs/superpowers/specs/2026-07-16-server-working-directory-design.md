# Server Working Directory Design

## Goal

Ensure the API server can register and serve the relative `studio.html` frontend route when it is launched by the Windows scheduled task.

## Design

The backend continues to use its existing relative frontend path. The scheduled task's command changes into the project directory before running the existing virtual-environment Python executable and `api_server.py`.

No absolute filesystem path is added to backend code. The task command determines the working directory at launch time, so `Path("studio.html")` resolves as intended and the `/` and `/studio` routes register normally.

## Validation

Restart the scheduled task, request `GET /`, and verify it returns the frontend HTML with HTTP 200. Also verify `GET /api/health` remains healthy.
