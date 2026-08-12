# Visual Asset Extraction Design

## Goal

Separate reusable visual assets from the shot list so the system generates
reference images only for on-screen characters and distinct locations.

## Current Problem

`ScriptBreakdown.scenes` represents timed shots, but the current visual and
image stages also treat every item as a unique scene asset. This produces
duplicate location images and can create character references for voices,
recordings, photographs, and mentioned-only people.

## Data Contract

Keep `ScriptBreakdown.scenes` as the compatibility-preserving shot list and
add an optional asset manifest:

- `VisualCharacterAsset`: stable ID, display name, `appearance_mode`
  (`on_screen`, `voice_only`, `still_photo`, or `mentioned`), Chinese and
  English appearance anchors, and optional disguise/costume states.
- `LocationAsset`: stable ID, canonical location, time/weather, immutable
  environment anchors, aliases, and a Chinese image prompt.
- `ShotVisualBinding`: `scene_id`, `location_id`, `on_screen_character_ids`,
  `voice_only_speakers`, and visible prop/state notes.

Only `on_screen` entries produce character reference images. Only location
assets produce scene reference images. The shot list keeps all story beats,
camera instructions, dialogue, and video generation state.

## Extraction Rules

The script-analysis LLM must produce the asset manifest in the same structured
response as the shot list. It must:

1. Add a character asset only when the person is physically visible in at
   least one generated shot and requires visual consistency.
2. Classify narration, telephone voices, recordings, speakers outside frame,
   mentioned people, and people appearing only in a photo as non-on-screen.
3. Preserve a single identity for disguised or masked characters; represent
   disguise as a shot state rather than a second character asset.
4. Deduplicate locations by physical space, time/weather, and fixed dressing.
   Camera angle, action, focal object, and actor placement do not create a
   new location asset.
5. Bind every shot to an existing location asset and only its visible
   character assets. Voices remain audio metadata and are not video subjects.

## Prompt Responsibilities

`ScriptAgent` owns the shot list plus asset manifest. `VisualPromptAgent`
accepts that manifest as authoritative and must not discover additional
characters or locations. It uses bindings to compose video prompts:

- on-screen characters become subject anchors;
- voice-only speakers are emitted as audio/dialogue context only;
- location anchors come from the referenced location asset;
- each prompt preserves shot-specific action, camera, and timing.

## Validation

Before asset image generation, validate that:

- every on-screen character is bound by at least one shot;
- no non-on-screen character has character image assets;
- every location asset is used by at least one shot;
- every shot has exactly one valid location binding;
- video prompt subjects match the shot's on-screen character bindings.

The validation report is returned to the UI and blocks only invalid image
generation, not script viewing or manual correction.

## UI Behavior

The role page lists visual character assets only. Voice-only and photo-only
people remain visible in the structured script with a non-visual label. The
scene page lists deduplicated location assets and the number of bound shots.
The shot page continues to show every shot and its referenced characters and
location.

## Migration

Existing projects remain readable. When no manifest exists, a compatibility
adapter derives candidate assets from the saved shot list and marks them as
legacy. Existing images, videos, paths, and history are never deleted. A user
can explicitly re-extract assets to create the normalized manifest.

## Expected Result For The Current Sample

The sample with 15 shots yields three visual character assets (Su Mian, Gu
Heng, Lu Lin) and five location assets (convenience-store entrance, alley,
underground entrance, underground passage, clocktower basement). Su Yuanshan
is `still_photo` or `voice_only`; a mysterious voice is `voice_only`.
