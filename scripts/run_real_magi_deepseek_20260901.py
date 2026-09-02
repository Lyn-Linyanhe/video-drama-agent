# -*- coding: utf-8 -*-
"""Reproducible official DeepSeek storyboard quality runner.

This script is intentionally separate from the product pipeline.  It performs
script parsing and storyboard generation only; it never calls Kimi, generates
video, or writes into ``output/``.  Network failures are recorded as safe,
credential-redacted evidence instead of being mistaken for a parsing result.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_storyboard_quality_check import audit_storyboard_quality
from src.agents.script_agent import ScriptAgent
from src.agents.visual_prompt_agent import VisualPromptAgent
from src.schemas.project import CreativeProfile
from src.config import LLM_REASONING_EFFORT
from src.services.timed_prompt_composer import apply_timed_prompt_structure
from src.utils.visual_asset_manifest import normalize_scene_assets

DEFAULT_LEGACY_SOURCE = ROOT / "tmp" / "magi_prompt_regen_20260901_full.txt"


def configured_reasoning_effort() -> str:
    """Return the actual configured effort instead of hard-coding the report."""
    return str(LLM_REASONING_EFFORT or "").strip().lower()


def build_source_text(source_path: Path) -> str:
    """Read an explicit source, supporting the old SCRIPT/DIALOGUE fixture format."""
    text = source_path.read_text(encoding="utf-8")
    if not re.search(r"^SCRIPT:\s*", text, flags=re.MULTILINE):
        return text

    scripts = re.findall(r"^SCRIPT:\s*(.*)$", text, flags=re.MULTILINE)
    dialogues = re.findall(r"^DIALOGUE:\s*(.*)$", text, flags=re.MULTILINE)
    if len(scripts) != len(dialogues):
        raise ValueError(
            f"legacy source blocks are unbalanced: scripts={len(scripts)}, dialogues={len(dialogues)}"
        )
    lines = ["《麦琪的礼物》"]
    for index, (script, dialogue) in enumerate(zip(scripts, dialogues), start=1):
        lines.append(f"{index}. {script}")
        if dialogue.strip():
            lines.append("对白：" + dialogue)
    return "\n".join(lines)


def safe_error_message(error: object) -> str:
    """Redact common API credentials before persisting or printing an error."""
    message = str(error or "未知错误")
    message = re.sub(r"sk-[A-Za-z0-9_-]+", "[REDACTED_KEY]", message)
    message = re.sub(r"(?i)(authorization\s*:\s*bearer\s+)[^\s,;]+", r"\1[REDACTED_TOKEN]", message)
    message = re.sub(r"(?i)(api[-_ ]?key\s*[:=]\s*)[^\s,;]+", r"\1[REDACTED_KEY]", message)
    return message[:2000]


def _agent_metadata(agent: object) -> dict[str, str]:
    llm = getattr(agent, "llm", None)
    return {
        "provider": str(getattr(llm, "provider", "") or ""),
        "model": str(getattr(llm, "model", "") or ""),
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_full_prompts(path: Path, prompts: object) -> None:
    shots = getattr(prompts, "shots", []) or []
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for shot in shots:
            handle.write(f"\n===== SCENE {shot.scene_id} ({shot.duration_sec}s) =====\n")
            handle.write(str(getattr(shot, "prompt", "") or "") + "\n")


async def run_real_storyboard_generation(source_text: str, *, out_dir: Path) -> dict[str, Any]:
    """Run the official DeepSeek parse -> visual prompt chain and save evidence.

    ``out_dir`` is used only for diagnostic JSON and full-prompt text.  The
    function does not call Kimi and does not write any project state.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    script_telemetry: dict[str, Any] = {}
    visual_telemetry: dict[str, Any] = {}
    script_agent = ScriptAgent()
    visual_agent = VisualPromptAgent()
    metadata = _agent_metadata(script_agent)
    profile = CreativeProfile(
        genre_tags=["经典文学", "爱情", "剧情"],
        art_style="电影级真人写实、复古贫困公寓质感",
        video_ratio="9:16",
    )
    try:
        script_started = time.perf_counter()
        script = await script_agent.analyze_existing_script(
            source_text, creative_profile=profile, telemetry=script_telemetry
        )
        script_elapsed = time.perf_counter() - script_started
        normalize_scene_assets(script)
        parse_snapshot = script.model_dump()
        _write_json(
            out_dir / "storyboard_parse.json",
            {
                "provider": metadata["provider"],
                "model": metadata["model"],
                "reasoning_effort": configured_reasoning_effort(),
                "source_text": source_text,
                "telemetry": script_telemetry,
                "script": parse_snapshot,
            },
        )

        visual_started = time.perf_counter()
        visual, prompts = await visual_agent.run(
            script,
            creative_profile=profile,
            telemetry=visual_telemetry,
            source_text=source_text,
        )
        visual_elapsed = time.perf_counter() - visual_started
        apply_timed_prompt_structure(prompts.shots, script)
        quality = audit_storyboard_quality(script, prompts)
        elapsed = time.perf_counter() - started
        payload: dict[str, Any] = {
            "ok": True,
            "test_type": "real_deepseek_script_parse_and_storyboard_generation",
            "generated_at": time.strftime("%Y-%m-%d"),
            **metadata,
            "reasoning_effort": configured_reasoning_effort(),
            "base_url_class": "official_deepseek_api",
            "source_text": source_text,
            "elapsed_sec": round(elapsed, 2),
            "script_elapsed_sec": round(script_elapsed, 2),
            "visual_elapsed_sec": round(visual_elapsed, 2),
            "script_telemetry": script_telemetry,
            "visual_telemetry": visual_telemetry,
            "script": parse_snapshot,
            "visual_design": visual.model_dump(),
            "generation_prompts": prompts.model_dump(),
            "quality_audit": quality,
        }
        _write_json(out_dir / "storyboard_result.json", payload)
        _write_full_prompts(out_dir / "storyboard_prompts.txt", prompts)
        return payload
    except Exception as exc:  # noqa: BLE001 - evidence runner must record external failures
        payload = {
            "ok": False,
            "test_type": "real_deepseek_script_parse_and_storyboard_generation",
            "generated_at": time.strftime("%Y-%m-%d"),
            **metadata,
            "reasoning_effort": configured_reasoning_effort(),
            "base_url_class": "official_deepseek_api",
            "elapsed_sec": round(time.perf_counter() - started, 2),
            "error_type": type(exc).__name__,
            "error": safe_error_message(exc),
            "script_telemetry": script_telemetry,
            "visual_telemetry": visual_telemetry,
        }
        _write_json(out_dir / "storyboard_result.json", payload)
        return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Official DeepSeek storyboard quality runner")
    parser.add_argument("--source", type=Path, default=DEFAULT_LEGACY_SOURCE)
    parser.add_argument("--out", type=Path, required=True, help="result JSON path; only tmp/ is recommended")
    args = parser.parse_args()
    source_text = build_source_text(args.source)
    payload = asyncio.run(run_real_storyboard_generation(source_text, out_dir=args.out.parent))
    _write_json(args.out, payload)
    print(json.dumps({
        key: payload.get(key)
        for key in ("ok", "provider", "model", "reasoning_effort", "elapsed_sec", "error_type", "error")
        if key in payload
    }, ensure_ascii=False, indent=2))
    if not payload.get("ok"):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
