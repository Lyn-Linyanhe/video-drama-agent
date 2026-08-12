# Image Asset Versioning Design

## Goal

Restore migrated images reliably and let users edit a character or scene image
prompt, regenerate its active image, upload a replacement, and switch to any
previous version.

## Data Model

Each project keeps stable active files in:

`output/<project_id>/assets/{characters,scenes}/...`

Replaced active files are moved into a sibling `history/` directory. The
project-level `assets/asset_manifest.json` records each version's asset key,
path, source (`generated` or `uploaded`), prompt, created timestamp, and
active flag. Existing state JSON remains the source for the active image shown
to the rest of the pipeline and is updated after every asset operation.

## API

The server exposes focused endpoints to fetch version history, regenerate one
active image from an edited prompt, upload a replacement for one active image,
and make a historical version active. Project IDs and asset identifiers are
validated before filesystem access.

## UI

Selecting a role or scene card opens an editor containing the current image,
view selector, editable prompt, regenerate command, upload command, and a
history grid. Choosing a version immediately makes it active. Scanned canonical
images take precedence over obsolete state paths when reopening a project.

## Duration Display

The production duration list is rendered from the editable storyboard shots.
Recovered video files are only playback assets and never overwrite storyboard
durations.
