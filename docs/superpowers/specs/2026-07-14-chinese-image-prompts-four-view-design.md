# Chinese Image Prompts and Four-View Character Assets

## Goal

Use Chinese for all character and scene image-generation prompts. Generate four
separate, reusable character reference images per character rather than a single
multi-panel sheet.

## Character Views

Each character has four independent assets: `front_half`, `front_full`,
`left_profile`, and `back_full`. The shared prompt establishes the role's
appearance, hair, physique, clothing, and visual style. Each view adds only its
camera and framing instruction.

All four images use the same deterministic character seed, white studio
background, neutral lighting, empty hands, no other people, and no text or
watermark. Each asset is a single view, not a collage.

## Compatibility and Downstream Use

New projects store the four names above. Existing `portrait`, `profile`, and
`full_body` assets remain readable and editable. Character cards prefer
`front_half`; scene/video reference selection prefers `front_full`, then falls
back to legacy full-body, portrait, and profile views.

## Scene Images

Scene prompt generation uses Chinese structured descriptions of spatial layout,
architecture, materials, lighting, objects, composition, mood, and vertical
framing. Wide and medium empty-environment images share this base description
and use Chinese negative prompts that forbid people, text, watermarks, and UI.
