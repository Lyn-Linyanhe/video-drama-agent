"""Gradio Web UI — 多智能体视频短剧生成器（分步交互版）"""

# ── Windows 编码兼容补丁（必须在所有 import 之前执行） ──────────
import sys as _sys
if hasattr(_sys.stdout, 'reconfigure'):
    _sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(_sys.stderr, 'reconfigure'):
    _sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# ── 代理清理（避免 Gradio 启动时被 Windows 代理阻塞） ──────────
import os as _os
_os.environ.setdefault('HTTP_PROXY', '')
_os.environ.setdefault('HTTPS_PROXY', '')
_os.environ.setdefault('NO_PROXY', '*')
_os.environ.setdefault('GRADIO_ANALYTICS_ENABLED', 'False')
_os.environ.setdefault('HF_HUB_DISABLE_TELEMETRY', '1')
_os.environ.setdefault('HF_HUB_OFFLINE', '1')
import urllib.request as _ur
_ur.getproxies = lambda: {}
# ────────────────────────────────────────────────────────────────

import gradio as gr
from pathlib import Path
import uuid
import json

from src.agents import (
    ScriptAgent, VisualDesignAgent, PromptEngineeringAgent,
    VideoGenerationAgent, ReviewAgent, AudioAgent, AssemblyAgent,
    CharacterAgent, SceneImageAgent,
)
from src.graph.orchestrator import DramaOrchestrator
from src.graph.state import DramaState
from src.api_clients.kling import KlingVideoClient
from src.api_clients.mock import MockVideoClient
from src.api_clients.image_mock import MockImageClient
from src.config import KLING_ACCESS_KEY, KLING_SECRET_KEY, KLING_MODEL, KLING_MODE, DEFAULT_API
from src.utils.project_store import ProjectStore

# ── 智能体单例（进程内复用，避免重复初始化） ────────────────────
script_agent = ScriptAgent()
visual_agent = VisualDesignAgent()
prompt_agent = PromptEngineeringAgent()
character_agent = CharacterAgent(api_client=MockImageClient())
scene_image_agent = SceneImageAgent(api_client=MockImageClient())

if DEFAULT_API == "kling" and KLING_ACCESS_KEY and KLING_SECRET_KEY:
    video_gen_agent = VideoGenerationAgent(api_client=KlingVideoClient(
        access_key=KLING_ACCESS_KEY, secret_key=KLING_SECRET_KEY,
        model=KLING_MODEL, mode=KLING_MODE,
    ))
else:
    video_gen_agent = VideoGenerationAgent()

review_agent = ReviewAgent()
audio_agent = AudioAgent()
assembly_agent = AssemblyAgent()

orchestrator = DramaOrchestrator(
    script_agent=script_agent, visual_agent=visual_agent,
    prompt_agent=prompt_agent, video_gen_agent=video_gen_agent,
    review_agent=review_agent, audio_agent=audio_agent,
    assembly_agent=assembly_agent,
    character_agent=character_agent, scene_image_agent=scene_image_agent,
)

project_store = ProjectStore()


# ═══════════════════════════════════════════════════════════════
#  全局样式 — 现代化、克制、注重留白
# ═══════════════════════════════════════════════════════════════
CUSTOM_CSS = """
/* ══ 全局基础 ═══════════════════════════════════════════ */
:root {
  --c-primary: #6366f1;
  --c-primary-2: #8b5cf6;
  --c-primary-3: #ec4899;
  --c-bg: #f6f7fb;
  --c-card: #ffffff;
  --c-border: #eef0f3;
  --c-text: #1f2937;
  --c-text-soft: #6b7280;
  --c-text-muted: #9ca3af;
  --shadow-sm: 0 1px 2px 0 rgb(0 0 0 / 0.05);
  --shadow-md: 0 4px 16px -4px rgb(0 0 0 / 0.08);
  --radius-sm: 8px;
  --radius: 12px;
  --radius-lg: 16px;
}

body, .gradio-container {
  font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", "Segoe UI", sans-serif !important;
  background: var(--c-bg) !important;
  color: var(--c-text);
}

/* ══ 顶部紧凑条 ═════════════════════════════════════════ */
.app-header {
  background: linear-gradient(135deg, #4f46e5 0%, #7c3aed 50%, #be185d 100%);
  padding: 10px 18px;
  border-radius: var(--radius);
  color: #ffffff;
  margin-bottom: 8px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  box-shadow: 0 2px 12px -2px rgba(79, 70, 229, 0.35);
}
.app-header h1 {
  margin: 0; font-size: 16px; font-weight: 700;
  text-shadow: 0 1px 4px rgba(0,0,0,0.3);
  letter-spacing: 0.5px;
}
.app-header .stat-row {
  display: flex; gap: 16px;
  font-size: 11px;
  text-shadow: 0 1px 3px rgba(0,0,0,0.3);
}
.app-header .stat b {
  font-size: 13px; font-weight: 700;
  color: #fbbf24;
}

/* ══ 顶部时间轴（紧凑） ═════════════════════════════════ */
.timeline-wrap {
  background: white;
  border-radius: var(--radius);
  padding: 10px 18px 8px 18px;
  box-shadow: var(--shadow-sm);
  border: 1px solid var(--c-border);
  margin-bottom: 8px;
}
.timeline-header {
  display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 8px;
}
.timeline-title { font-size: 12px; font-weight: 600; color: var(--c-text); }
.timeline-sub { font-size: 10px; color: var(--c-text-muted); }
.timeline-progress {
  font-size: 11px; color: var(--c-text-soft);
  padding: 2px 10px; background: #f3f4f6; border-radius: 999px;
}
.timeline-progress b { color: var(--c-primary); font-size: 13px; }

.timeline {
  display: grid; grid-template-columns: repeat(6, 1fr); gap: 0;
  position: relative;
}
.timeline-step {
  position: relative;
  display: flex; flex-direction: column; align-items: center;
  cursor: pointer; padding: 2px 4px;
  transition: transform 0.2s ease;
}
.timeline-step:hover { transform: translateY(-1px); }
.timeline-step .dot {
  width: 30px; height: 30px; border-radius: 50%;
  background: #f3f4f6; color: var(--c-text-muted);
  display: flex; align-items: center; justify-content: center;
  font-size: 13px; font-weight: 600;
  border: 2px solid var(--c-border);
  position: relative; z-index: 2;
  transition: all 0.3s ease;
}
.timeline-step .label {
  margin-top: 4px; font-size: 10px;
  color: var(--c-text-muted); text-align: center;
  font-weight: 500; line-height: 1.3;
}
.timeline-step .sublabel {
  font-size: 9px; color: #d1d5db; margin-top: 1px;
  display: none;
}
.timeline-step::after {
  content: ""; position: absolute;
  top: 15px; left: 50%; width: 100%; height: 2px;
  background: var(--c-border); z-index: 1;
}
.timeline-step:last-child::after { display: none; }
.timeline-step.done .dot {
  background: linear-gradient(135deg, #10b981, #059669);
  color: white; border-color: #10b981;
  box-shadow: 0 2px 6px -2px rgba(16, 185, 129, 0.5);
}
.timeline-step.done .label { color: #10b981; font-weight: 600; }
.timeline-step.done::after { background: #10b981; }
.timeline-step.active .dot {
  background: linear-gradient(135deg, #6366f1, #8b5cf6);
  color: white; border-color: #6366f1;
  box-shadow: 0 0 0 4px rgba(99, 102, 241, 0.15), 0 2px 6px -2px rgba(99, 102, 241, 0.5);
}
.timeline-step.active .label { color: var(--c-primary); font-weight: 600; }
.timeline-step.locked { cursor: not-allowed; opacity: 0.5; }
.timeline-step.locked:hover { transform: none; }

/* ══ 故事输入卡 ═══════════════════════════════════════ */
.story-input-card {
  background: linear-gradient(135deg, #fffbeb 0%, #fce7f3 50%, #ede9fe 100%);
  border: 1px solid #fbbf24;
  border-radius: var(--radius);
  padding: 12px 18px;
  margin-bottom: 8px;
}
.story-input-card h2 {
  margin: 0 0 2px 0; font-size: 14px; font-weight: 700;
  color: #92400e;
}
.story-input-card .hint {
  font-size: 11px; color: #b45309; margin-bottom: 8px;
}
.story-input-card textarea {
  background: white !important;
  border: 1.5px solid #fcd34d !important;
  border-radius: var(--radius) !important;
  font-size: 13px !important;
}
.story-input-card textarea:focus {
  border-color: #f59e0b !important;
  box-shadow: 0 0 0 3px rgba(245, 158, 11, 0.15) !important;
}

/* ══ 主操作按钮（短剧 App 风） ═══════════════════════════ */
.cta-btn {
  background: linear-gradient(135deg, #6366f1 0%, #ec4899 100%) !important;
  border: none !important;
  color: white !important;
  font-weight: 600 !important;
  font-size: 15px !important;
  border-radius: var(--radius) !important;
  box-shadow: 0 4px 14px -2px rgba(236, 72, 153, 0.35) !important;
  transition: all 0.2s ease !important;
}
.cta-btn:hover {
  transform: translateY(-1px) !important;
  box-shadow: 0 6px 20px -2px rgba(236, 72, 153, 0.5) !important;
}

/* ══ 步骤按钮（紧凑型） ═════════════════════════════════ */
.step-btn {
  background: white !important;
  border: 1.5px solid var(--c-border) !important;
  color: var(--c-text) !important;
  font-weight: 500 !important;
  border-radius: var(--radius) !important;
  transition: all 0.2s ease !important;
}
.step-btn:hover {
  border-color: var(--c-primary) !important;
  color: var(--c-primary) !important;
  transform: translateY(-1px);
}

/* ══ 主结果大卡（短剧内容墙） ═══════════════════════════ */
.content-wall {
  background: var(--c-card);
  border-radius: var(--radius-lg);
  border: 1px solid var(--c-border);
  box-shadow: var(--shadow-sm);
  padding: 0;
  margin-bottom: 14px;
  overflow: hidden;
}
.wall-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 18px;
  border-bottom: 1px solid var(--c-border);
  background: linear-gradient(90deg, #fafbff, #fef2ff);
}
.wall-title {
  display: flex; align-items: center; gap: 8px;
  font-size: 14px; font-weight: 600; color: var(--c-text);
}
.wall-title .icon {
  width: 26px; height: 26px; border-radius: 6px;
  display: flex; align-items: center; justify-content: center;
  font-size: 14px;
  background: linear-gradient(135deg, #eef2ff, #fce7f3);
}
.wall-body { padding: 12px 18px; min-height: 200px; }

/* ══ 9:16 竖屏卡片墙（短剧核心） ═════════════════════════ */
.portrait-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 14px;
}
.portrait-card {
  position: relative;
  background: #1f2937;
  border-radius: var(--radius);
  overflow: hidden;
  aspect-ratio: 9 / 16;
  box-shadow: var(--shadow-md);
  transition: all 0.3s ease;
  cursor: pointer;
}
.portrait-card:hover { transform: translateY(-3px); box-shadow: 0 8px 24px -4px rgb(0 0 0 / 0.15); }
.portrait-card img, .portrait-card video {
  width: 100%; height: 100%;
  object-fit: cover;
}
.portrait-card .portrait-overlay {
  position: absolute; bottom: 0; left: 0; right: 0;
  padding: 24px 12px 10px 12px;
  background: linear-gradient(to top, rgba(0,0,0,0.85), transparent);
  color: white; font-size: 12px;
}
.portrait-card .portrait-overlay .name {
  font-weight: 600; font-size: 13px; margin-bottom: 2px;
}
.portrait-card .portrait-overlay .meta {
  font-size: 11px; opacity: 0.85;
}
.portrait-card .portrait-badge {
  position: absolute; top: 8px; left: 8px;
  background: rgba(0,0,0,0.6); color: white;
  font-size: 10px; padding: 2px 8px; border-radius: 999px;
  font-weight: 500;
}
.portrait-card .portrait-action {
  position: absolute; top: 8px; right: 8px;
  width: 28px; height: 28px; border-radius: 50%;
  background: rgba(255,255,255,0.95);
  display: flex; align-items: center; justify-content: center;
  font-size: 12px; cursor: pointer;
  opacity: 0; transition: opacity 0.2s ease;
}
.portrait-card:hover .portrait-action { opacity: 1; }
.portrait-empty {
  aspect-ratio: 9 / 16;
  background: #f3f4f6; border: 2px dashed var(--c-border);
  border-radius: var(--radius);
  display: flex; align-items: center; justify-content: center;
  color: var(--c-text-muted); font-size: 12px;
  flex-direction: column; gap: 4px;
}

/* ══ 沉浸式视频播放器 ═══════════════════════════════════ */
.immersive-player {
  background: #000; border-radius: var(--radius);
  aspect-ratio: 9 / 16; max-height: 540px;
  display: flex; align-items: center; justify-content: center;
  margin: 0 auto; max-width: 360px;
  position: relative; overflow: hidden;
  box-shadow: 0 12px 40px -8px rgb(0 0 0 / 0.4);
}
.immersive-player video { width: 100%; height: 100%; object-fit: contain; }
.immersive-player .player-fallback {
  color: rgba(255,255,255,0.5); font-size: 14px;
  text-align: center; padding: 20px;
}

/* ══ 剧本分镜卡（横向） ═════════════════════════════════ */
.scene-strip {
  display: flex; gap: 12px; overflow-x: auto;
  padding: 6px 0 12px 0;
  scroll-snap-type: x mandatory;
}
.scene-strip::-webkit-scrollbar { height: 6px; }
.scene-strip::-webkit-scrollbar-thumb { background: var(--c-border); border-radius: 3px; }
.scene-card {
  flex: 0 0 200px;
  background: white;
  border: 1px solid var(--c-border);
  border-radius: var(--radius);
  padding: 12px;
  scroll-snap-align: start;
  transition: all 0.2s ease;
}
.scene-card:hover { border-color: var(--c-primary); transform: translateY(-2px); }
.scene-card .scene-num {
  font-size: 10px; color: var(--c-primary);
  font-weight: 600; letter-spacing: 0.5px;
}
.scene-card .scene-loc {
  font-size: 14px; font-weight: 600; margin: 4px 0;
  color: var(--c-text);
}
.scene-card .scene-meta {
  font-size: 11px; color: var(--c-text-soft);
  display: flex; gap: 6px; flex-wrap: wrap;
}
.scene-card .scene-emotion {
  display: inline-block; padding: 1px 8px;
  background: #fef3c7; color: #b45309;
  border-radius: 999px; font-size: 10px; font-weight: 500;
}
.scene-card .scene-line {
  font-size: 11px; color: var(--c-text-muted);
  margin-top: 6px; font-style: italic;
  display: -webkit-box; -webkit-line-clamp: 2;
  -webkit-box-orient: vertical; overflow: hidden;
}

/* ══ 提示词卡（编辑用） ═════════════════════════════════ */
.prompt-card {
  background: white; border: 1.5px solid var(--c-border);
  border-radius: var(--radius); padding: 16px;
  margin-bottom: 12px; transition: all 0.2s ease;
}
.prompt-card:hover { border-color: var(--c-primary); box-shadow: var(--shadow-sm); }
.prompt-card .prompt-head {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 10px;
}
.prompt-card .prompt-num {
  font-size: 13px; font-weight: 600; color: var(--c-primary);
  display: flex; align-items: center; gap: 8px;
}
.prompt-card .prompt-num .chip {
  font-size: 10px; padding: 2px 8px;
  background: #eef2ff; color: var(--c-primary);
  border-radius: 999px; font-weight: 500;
}
.prompt-card textarea {
  font-family: "JetBrains Mono", "Cascadia Code", Consolas, monospace !important;
  font-size: 12px !important; line-height: 1.6 !important;
  border: 1px solid var(--c-border) !important;
  border-radius: var(--radius-sm) !important;
}
.prompt-card textarea:focus {
  border-color: var(--c-primary) !important;
  box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.1) !important;
}
.prompt-card .prompt-actions {
  display: flex; gap: 6px; margin-top: 8px;
}
.prompt-card .pill {
  display: inline-block; padding: 2px 8px;
  background: #f3f4f6; color: var(--c-text-soft);
  border-radius: 999px; font-size: 10px;
  font-family: monospace;
}

/* ══ 状态徽标 ═══════════════════════════════════════════ */
.status-badge {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 3px 10px; border-radius: 999px;
  font-size: 11px; font-weight: 500;
}
.status-badge.success { background: #d1fae5; color: #065f46; }
.status-badge.warning { background: #fef3c7; color: #92400e; }
.status-badge.error { background: #fee2e2; color: #991b1b; }
.status-badge.info { background: #dbeafe; color: #1e40af; }
.status-badge.muted { background: #f3f4f6; color: #6b7280; }

/* ══ 空状态 ═════════════════════════════════════════════ */
.empty-state {
  text-align: center; padding: 30px 20px;
  color: var(--c-text-muted); font-size: 12px;
}
.empty-state .icon { font-size: 24px; margin-bottom: 4px; opacity: 0.3; }

/* ══ 折叠面板 ═══════════════════════════════════════════ */
details summary { cursor: pointer; user-select: none; }

/* ══ 日志区 ═════════════════════════════════════════════ */
.log-box textarea {
  font-family: "JetBrains Mono", "Cascadia Code", Consolas, monospace !important;
  font-size: 11px !important; line-height: 1.55 !important;
  background: #f8fafc !important;
}

/* ══ 通用按钮 / 链接 ═══════════════════════════════════ */
a { color: var(--c-primary); text-decoration: none; }

/* ══ 隐藏 Gradio 默认 footer ═══════════════════════════ */
footer { display: none !important; }

/* ══ 响应式：小屏适配 ═════════════════════════════════ */
@media (max-width: 1100px) {
  .wall-header { flex-direction: column; align-items: flex-start; gap: 8px; }
}

/* ── 全局基础 ─────────────────────────────────────────────── */
:root {
  --c-primary: #6366f1;
  --c-primary-soft: #eef2ff;
  --c-success: #10b981;
  --c-success-soft: #ecfdf5;
  --c-warning: #f59e0b;
  --c-warning-soft: #fffbeb;
  --c-muted: #6b7280;
  --c-muted-soft: #f9fafb;
  --c-border: #e5e7eb;
  --c-text: #1f2937;
  --c-text-soft: #4b5563;
  --shadow-sm: 0 1px 2px 0 rgb(0 0 0 / 0.05);
  --shadow-md: 0 4px 12px -2px rgb(0 0 0 / 0.08);
  --radius: 10px;
}

body, .gradio-container {
  font-family: -apple-system, "PingFang SC", "Microsoft YaHei", "Segoe UI", sans-serif !important;
  background: #fafbfc !important;
}

/* ── 标题与分组 ───────────────────────────────────────────── */
.section-title {
  font-size: 15px !important;
  font-weight: 600 !important;
  color: var(--c-text) !important;
  margin: 18px 0 10px 0 !important;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--c-border);
  display: flex;
  align-items: center;
  gap: 8px;
}

.section-title .num {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px; height: 22px;
  border-radius: 50%;
  background: var(--c-primary);
  color: white;
  font-size: 12px;
  font-weight: 600;
}

/* ── 阶段按钮组 ───────────────────────────────────────────── */
.stage-row { gap: 8px !important; }
.stage-btn { transition: all 0.2s ease !important; }
.stage-btn:hover { transform: translateY(-1px); box-shadow: var(--shadow-md); }

/* ── 结果面板（卡片化） ──────────────────────────────────── */
.result-panel {
  background: white;
  border: 1px solid var(--c-border);
  border-radius: var(--radius);
  padding: 16px 18px;
  margin: 10px 0;
  box-shadow: var(--shadow-sm);
  transition: box-shadow 0.2s ease;
}
.result-panel:hover { box-shadow: var(--shadow-md); }

.result-panel .panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
  padding-bottom: 10px;
  border-bottom: 1px dashed var(--c-border);
}
.result-panel .panel-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--c-text);
}
.result-panel .panel-badge {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--c-primary-soft);
  color: var(--c-primary);
  font-weight: 500;
}

.empty-state {
  text-align: center;
  padding: 40px 20px;
  color: var(--c-muted);
  font-size: 13px;
}

/* ── 阶段状态徽标 ────────────────────────────────────────── */
.stage-pill {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 500;
  margin-left: 6px;
}
.stage-pill.done    { background: var(--c-success-soft); color: var(--c-success); }
.stage-pill.active  { background: var(--c-warning-soft); color: var(--c-warning); }
.stage-pill.locked  { background: var(--c-muted-soft);   color: var(--c-muted); }

/* ── 提示词编辑框（等宽字体） ────────────────────────────── */
.prompt-edit textarea {
  font-family: "JetBrains Mono", "Cascadia Code", Consolas, monospace !important;
  font-size: 12px !important;
  line-height: 1.6 !important;
}

/* ── 日志区（等宽+紧凑） ────────────────────────────────── */
.log-box textarea {
  font-family: "JetBrains Mono", "Cascadia Code", Consolas, monospace !important;
  font-size: 11px !important;
  line-height: 1.55 !important;
  background: #f8fafc !important;
}

/* ── 输入框（更柔和的边框） ─────────────────────────────── */
textarea, input[type="text"] {
  border-radius: 8px !important;
}

/* ── 主按钮（梯度） ─────────────────────────────────────── */
.primary-btn {
  background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%) !important;
  border: none !important;
  color: white !important;
  font-weight: 500 !important;
}

/* ── 资产库卡片 ─────────────────────────────────────────── */
.asset-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 12px;
  padding: 8px 0;
}
.asset-card {
  background: white;
  border: 1px solid var(--c-border);
  border-radius: 8px;
  overflow: hidden;
  transition: all 0.2s ease;
}
.asset-card:hover { transform: translateY(-2px); box-shadow: var(--shadow-md); }
.asset-card .asset-thumb {
  width: 100%;
  aspect-ratio: 9/16;
  background: #f3f4f6;
  object-fit: cover;
}
.asset-card .asset-meta {
  padding: 8px 10px;
  font-size: 12px;
  color: var(--c-text-soft);
}

/* ── Tab 样式（更精致） ─────────────────────────────────── */
.tab-nav button {
  font-size: 14px !important;
  font-weight: 500 !important;
}

/* ── 隐藏 Gradio 默认 footer ────────────────────────────── */
footer { display: none !important; }

/* ══ 顶部水平时间轴（短剧 App 风格） ═══════════════════════ */
.timeline-wrap {
  background: white;
  border-radius: 14px;
  padding: 22px 28px 18px 28px;
  box-shadow: 0 2px 12px -2px rgb(0 0 0 / 0.06);
  border: 1px solid #eef0f3;
  margin-bottom: 18px;
  position: relative;
  overflow: hidden;
}
.timeline-wrap::before {
  content: "";
  position: absolute;
  top: 0; left: 0; right: 0; height: 3px;
  background: linear-gradient(90deg, #6366f1, #8b5cf6, #ec4899);
}
.timeline-header {
  display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 22px;
}
.timeline-title {
  font-size: 18px; font-weight: 600; color: #1f2937;
}
.timeline-sub {
  font-size: 12px; color: #9ca3af; margin-top: 2px;
}
.timeline-progress {
  display: flex; align-items: center; gap: 4px;
  font-size: 13px; color: #6b7280;
  padding: 4px 12px; background: #f3f4f6; border-radius: 999px;
}
.timeline-progress b { color: #6366f1; font-size: 15px; }

.timeline {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 0;
  position: relative;
}
.timeline-step {
  position: relative;
  display: flex; flex-direction: column; align-items: center;
  cursor: pointer;
  padding: 4px 6px;
  transition: transform 0.2s ease;
}
.timeline-step:hover { transform: translateY(-2px); }
.timeline-step .dot {
  width: 44px; height: 44px;
  border-radius: 50%;
  background: #f3f4f6;
  color: #9ca3af;
  display: flex; align-items: center; justify-content: center;
  font-size: 18px; font-weight: 600;
  border: 2px solid #e5e7eb;
  position: relative; z-index: 2;
  transition: all 0.3s ease;
}
.timeline-step .label {
  margin-top: 10px;
  font-size: 12px;
  color: #9ca3af;
  text-align: center;
  font-weight: 500;
  line-height: 1.4;
}
.timeline-step .sublabel {
  font-size: 10px;
  color: #d1d5db;
  margin-top: 2px;
}
/* 连接线（横向） */
.timeline-step::after {
  content: "";
  position: absolute;
  top: 22px; left: 50%;
  width: 100%; height: 2px;
  background: #e5e7eb;
  z-index: 1;
}
.timeline-step:last-child::after { display: none; }

/* 已完成 */
.timeline-step.done .dot {
  background: linear-gradient(135deg, #10b981, #059669);
  color: white; border-color: #10b981;
  box-shadow: 0 2px 8px -2px rgba(16, 185, 129, 0.5);
}
.timeline-step.done .label { color: #10b981; font-weight: 600; }
.timeline-step.done::after { background: #10b981; }

/* 当前激活 */
.timeline-step.active .dot {
  background: linear-gradient(135deg, #6366f1, #8b5cf6);
  color: white; border-color: #6366f1;
  box-shadow: 0 0 0 6px rgba(99, 102, 241, 0.15), 0 2px 8px -2px rgba(99, 102, 241, 0.5);
  animation: pulse 2s infinite;
}
.timeline-step.active .label { color: #6366f1; font-weight: 600; }
@keyframes pulse {
  0%, 100% { box-shadow: 0 0 0 6px rgba(99, 102, 241, 0.15), 0 2px 8px -2px rgba(99, 102, 241, 0.5); }
  50% { box-shadow: 0 0 0 10px rgba(99, 102, 241, 0.05), 0 2px 8px -2px rgba(99, 102, 241, 0.5); }
}

/* 锁定 */
.timeline-step.locked { cursor: not-allowed; opacity: 0.6; }
.timeline-step.locked:hover { transform: none; }

/* ── 主内容区 ─────────────────────────────────────────── */
.main-card {
  background: white;
  border-radius: 14px;
  border: 1px solid #eef0f3;
  box-shadow: 0 2px 12px -2px rgb(0 0 0 / 0.06);
  padding: 24px 28px;
  margin-bottom: 16px;
}
.input-card {
  background: linear-gradient(135deg, #fafbff 0%, #f5f3ff 100%);
  border: 1px solid #e0e7ff;
  border-radius: 14px;
  padding: 20px 24px;
  margin-bottom: 16px;
}
.input-card textarea {
  background: white !important;
  border: 1.5px solid #e0e7ff !important;
  border-radius: 10px !important;
}
.input-card textarea:focus {
  border-color: #6366f1 !important;
  box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.1) !important;
}

/* 短剧风格大按钮 */
.cta-btn {
  background: linear-gradient(135deg, #6366f1 0%, #ec4899 100%) !important;
  border: none !important;
  color: white !important;
  font-weight: 600 !important;
  font-size: 15px !important;
  padding: 14px 24px !important;
  border-radius: 12px !important;
  box-shadow: 0 4px 14px -2px rgba(236, 72, 153, 0.35) !important;
  transition: all 0.2s ease !important;
}
.cta-btn:hover {
  transform: translateY(-1px) !important;
  box-shadow: 0 6px 20px -2px rgba(236, 72, 153, 0.5) !important;
}

/* 主结果区大卡片 */
.hero-card {
  background: white;
  border-radius: 14px;
  border: 1px solid #eef0f3;
  box-shadow: 0 2px 12px -2px rgb(0 0 0 / 0.06);
  padding: 24px;
  min-height: 400px;
}
.hero-header {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 16px;
  padding-bottom: 14px;
  border-bottom: 1px solid #f3f4f6;
}
.hero-title {
  display: flex; align-items: center; gap: 10px;
  font-size: 16px; font-weight: 600; color: #1f2937;
}
.hero-title .icon {
  width: 32px; height: 32px;
  border-radius: 8px;
  display: flex; align-items: center; justify-content: center;
  font-size: 16px;
  background: linear-gradient(135deg, #eef2ff, #fce7f3);
}
"""


# ═══════════════════════════════════════════════════════════════
#  辅助函数
# ═══════════════════════════════════════════════════════════════

def 阶段状态(stages_done: list, stage_num: int, stage_name: str) -> str:
    """生成单步骤的状态标记徽标。"""
    if stage_name in stages_done:
        return f'<span class="stage-pill done">✓ 已完成</span>'
    elif len(stages_done) == stage_num - 1:
        return f'<span class="stage-pill active">▶ 可执行</span>'
    else:
        return f'<span class="stage-pill locked">🔒 等待中</span>'


def 全部阶段状态(stages_done: list) -> str:
    """生成 6 步进度概览。"""
    步骤列表 = [
        ("1", "生成剧本", "script"),
        ("2", "生成角色与场景图", "characters"),
        ("3", "生成视频提示词", "prompts"),
        ("4", "生成视频片段", "videos"),
        ("5", "生成音频", "audio"),
        ("6", "合成最终视频", "assembly"),
    ]
    行列表 = []
    for 序号, 名称, _ in 步骤列表:
        idx = int(序号)
        徽标 = 阶段状态(stages_done, idx, f"stage{idx}")
        if f"stage{idx}" in stages_done:
            emoji = "✅"
        elif len(stages_done) == idx - 1:
            emoji = "🟡"
        else:
            emoji = "⚪"
        行列表.append(f"{emoji} **第{序号}步 · {名称}** {徽标}")
    return "\n\n".join(行列表)


# ═══════════════════════════════════════════════════════════════
#  分阶段回调函数
# ═══════════════════════════════════════════════════════════════

async def 执行第1步(user_input: str, api_choice: str, aspect_ratio: str):
    """第1步：生成剧本 + 视觉设计。"""
    if not user_input.strip():
        yield "### ⚠️ 请先输入故事内容", "", "", gr.update(), "", [], ""
        return

    project_id = uuid.uuid4().hex[:12]
    project_store.create_project(
        title=user_input[:30] + ("…" if len(user_input) > 30 else ""),
        user_input=user_input,
    )
    project_store.update_status(project_id, "生成中")

    state = DramaState(user_input=user_input, project_id=project_id)
    state.log("🚀 第 1 步：编剧与视觉设计启动")

    try:
        result = await orchestrator.run_stage(1, state)
    except Exception as e:
        yield f"### ❌ 第 1 步失败：{e}", "", "", gr.update(), "", [], project_id
        return

    if result.error_message:
        yield f"### ❌ 失败：{result.error_message}", "", "", gr.update(), "", [], project_id
        return

    project_store.add_stage(project_id, "stage1")

    # 剧本摘要
    s = result.script
    场景表 = []
    for sc in s.scenes:
        台词 = ""
        if sc.dialogue:
            台词 = "; ".join(f"{d.character}: {d.line}" for d in sc.dialogue[:2])
        场景表.append(
            f"| {sc.scene_id} | {sc.location} | {sc.duration_sec}s | {sc.emotion_tone} | {sc.camera_direction} | {台词[:40]} |"
        )

    剧本摘要 = f"""### 📜 {s.title}

**类型** {s.genre} · **预估时长** {s.duration_sec} 秒 · **场景数** {len(s.scenes)}

> {s.logline}

| # | 地点 | 时长 | 情绪 | 镜头 | 台词 |
|---|------|------|------|------|------|
{chr(10).join(场景表)}

**整体视觉风格** {result.visual_design.overall_style}
**主色调** {result.visual_design.color_palette}
"""

    角色摘要 = "### 👥 角色列表\n\n"
    for name, desc in (result.visual_design.global_character_descriptions or {}).items():
        角色摘要 += f"- **{name}** — {desc[:120]}{'…' if len(desc) > 120 else ''}\n"

    log_text = "\n".join(result.execution_log)
    stages_done = ["stage1"]

    yield (
        "### ✅ 第 1 步完成 — 剧本与视觉设计已生成",
        剧本摘要,
        角色摘要,
        gr.update(value=result),
        log_text,
        stages_done,
        project_id,
    )


async def 执行第2步(state_json: str):
    """第2步：生成角色形象图 + 场景图。"""
    if not state_json:
        yield "### ⚠️ 请先完成第 1 步", "", "", gr.update(), "", [], ""
        return

    state = _state_from_json(state_json)
    state.log("🎨 第 2 步：角色形象与场景图生成中")

    try:
        result = await orchestrator.run_stage(2, state)
    except Exception as e:
        yield f"### ❌ 第 2 步失败：{e}", "", "", gr.update(), "", [], result.project_id
        return

    project_store.add_stage(result.project_id, "stage2")

    # 角色图
    角色_html = '<div class="result-panel"><div class="panel-header"><div class="panel-title">🎭 角色形象图</div><div class="panel-badge">{}</div></div>'.format(
        f"{len(result.character_images.characters)} 个角色" if result.character_images else "暂无"
    )
    if result.character_images and result.character_images.characters:
        角色_html += '<div class="asset-grid">'
        for img in result.character_images.characters:
            路径 = img.image_path
            if 路径 and Path(路径).exists():
                角色_html += (
                    f'<div class="asset-card">'
                    f'<img class="asset-thumb" src="file={路径}" />'
                    f'<div class="asset-meta"><b>{img.character_name}</b></div>'
                    f'</div>'
                )
            else:
                角色_html += f'<div class="asset-card"><div class="asset-meta">⚠️ {img.character_name} - 生成失败</div></div>'
        角色_html += '</div>'
    else:
        角色_html += '<div class="empty-state">尚无角色形象图</div>'
    角色_html += '</div>'

    # 场景图
    场景_html = '<div class="result-panel"><div class="panel-header"><div class="panel-title">🖼️ 场景环境图</div><div class="panel-badge">{}</div></div>'.format(
        f"{len(result.scene_images.scenes)} 个场景" if result.scene_images else "暂无"
    )
    if result.scene_images and result.scene_images.scenes:
        场景_html += '<div class="asset-grid">'
        for img in result.scene_images.scenes:
            路径 = img.image_path
            if 路径 and Path(路径).exists():
                场景_html += (
                    f'<div class="asset-card">'
                    f'<img class="asset-thumb" src="file={路径}" />'
                    f'<div class="asset-meta">场景 {img.scene_id}<br><span style="color:#6b7280">{img.location[:20]}</span></div>'
                    f'</div>'
                )
            else:
                场景_html += f'<div class="asset-card"><div class="asset-meta">⚠️ 场景 {img.scene_id} - 失败</div></div>'
        场景_html += '</div>'
    else:
        场景_html += '<div class="empty-state">尚无场景图</div>'
    场景_html += '</div>'

    log_text = "\n".join(result.execution_log)
    stages_done = ["stage1", "stage2"]

    yield (
        "### ✅ 第 2 步完成 — 角色形象与场景图已生成",
        角色_html,
        场景_html,
        gr.update(value=result),
        log_text,
        stages_done,
        result.project_id,
    )


async def 执行第3步(state_json: str, user_feedback: str = ""):
    """第3步：生成视频提示词（可编辑）。"""
    if not state_json:
        yield "### ⚠️ 请先完成前序步骤", "", gr.update(), gr.update(), "", [], ""
        return

    state = _state_from_json(state_json)
    state.user_feedback = user_feedback
    state.log("💬 第 3 步：生成视频提示词")

    try:
        result = await orchestrator.run_stage(3, state)
    except Exception as e:
        yield f"### ❌ 第 3 步失败：{e}", "", gr.update(), gr.update(), "", [], state.project_id
        return

    project_store.add_stage(result.project_id, "stage3")

    # 构建提示词表
    prompt_edits = {}
    if result.generation_prompts:
        for i, shot in enumerate(result.generation_prompts.shots):
            prompt_edits[str(i)] = shot.prompt

    log_text = "\n".join(result.execution_log)
    stages_done = ["stage1", "stage2", "stage3"]

    yield (
        "### ✅ 第 3 步完成 — 提示词已生成，可在下方编辑后再继续",
        json.dumps(prompt_edits, ensure_ascii=False),
        gr.update(value=result),
        gr.update(value=result),
        log_text,
        stages_done,
        result.project_id,
    )


async def AI修改提示词(state_json: str, prompt_edits_json: str, shot_index: str, feedback: str):
    """调用 LLM 根据用户反馈修改指定镜头的提示词。"""
    if not state_json or not feedback.strip():
        yield "### ⚠️ 请先提供修改建议", prompt_edits_json
        return

    state = _state_from_json(state_json)
    try:
        current_edits = json.loads(prompt_edits_json) if prompt_edits_json else {}
    except json.JSONDecodeError:
        current_edits = {}

    idx = int(shot_index) if shot_index else 0
    original_prompt = ""
    if state.generation_prompts and idx < len(state.generation_prompts.shots):
        original_prompt = state.generation_prompts.shots[idx].prompt

    revision_prompt = f"""用户对以下视频提示词有修改意见：

**原始提示词**：
{original_prompt}

**用户反馈**：
{feedback}

请根据用户反馈修改提示词，输出修改后的完整英文提示词（只输出 prompt 文本，不要其他解释）。"""

    try:
        llm = script_agent.llm
        new_prompt = await llm.generate_text(
            system_prompt="你是一个专业的 AI 视频生成提示词工程师，擅长根据用户反馈优化视频描述。只输出修改后的提示词。",
            user_prompt=revision_prompt,
            temperature=0.5,
        )
        new_prompt = new_prompt.strip().strip('"').strip("'")
        current_edits[str(idx)] = new_prompt
        yield f"### ✅ 已修改镜头 {idx+1} 的提示词\n\n```\n{new_prompt}\n```", json.dumps(current_edits, ensure_ascii=False)
    except Exception as e:
        yield f"### ❌ AI 修改失败：{e}", prompt_edits_json


async def 执行第4步(state_json: str, prompt_edits_json: str):
    """第4步：生成视频片段。"""
    if not state_json:
        yield "### ⚠️ 请先完成前序步骤", "", gr.update(), "", [], "", None
        return

    state = _state_from_json(state_json)
    try:
        edits = json.loads(prompt_edits_json) if prompt_edits_json else {}
        state.prompt_overrides = edits
    except json.JSONDecodeError:
        pass

    state.log("🎬 第 4 步：生成视频片段")

    try:
        result = await orchestrator.run_stage(4, state)
    except Exception as e:
        yield f"### ❌ 第 4 步失败：{e}", "", gr.update(), "", [], "", None
        return

    project_store.add_stage(result.project_id, "stage4")

    # 视频列表
    视频_html = '<div class="result-panel"><div class="panel-header"><div class="panel-title">🎬 视频片段</div>'
    if result.generated_assets:
        成功 = sum(1 for s in result.generated_assets.shots if s.success)
        总数 = len(result.generated_assets.shots)
        视频_html += f'<div class="panel-badge">{成功}/{总数} 成功 · 成本 ${result.generated_assets.total_cost:.4f}</div></div>'
        视频_html += '<div class="asset-grid">'
        for asset in result.generated_assets.shots:
            路径 = asset.local_path
            if asset.success and 路径 and Path(路径).exists():
                视频_html += (
                    f'<div class="asset-card">'
                    f'<video class="asset-thumb" controls preload="metadata" src="file={路径}"></video>'
                    f'<div class="asset-meta">场景 {asset.scene_id} · {asset.duration_sec:.1f}s</div>'
                    f'</div>'
                )
            else:
                视频_html += f'<div class="asset-card"><div class="asset-meta">❌ 场景 {asset.scene_id}<br><span style="color:#9ca3af;font-size:10px">{asset.error_message[:30]}</span></div></div>'
        视频_html += '</div>'
    else:
        视频_html += '<div class="empty-state">尚未生成视频</div>'
    视频_html += '</div>'

    log_text = "\n".join(result.execution_log)
    stages_done = ["stage1", "stage2", "stage3", "stage4"]

    yield (
        "### ✅ 第 4 步完成 — 视频片段已生成",
        视频_html,
        gr.update(value=result),
        log_text,
        stages_done,
        result.project_id,
        None,
    )


async def 执行第5步(state_json: str):
    """第5步：生成音频。"""
    if not state_json:
        yield "### ⚠️ 请先完成前序步骤", "", gr.update(), "", [], ""
        return

    state = _state_from_json(state_json)
    state.log("🔊 第 5 步：生成音频")

    try:
        result = await orchestrator.run_stage(5, state)
    except Exception as e:
        yield f"### ❌ 第 5 步失败：{e}", "", gr.update(), "", [], state.project_id
        return

    project_store.add_stage(result.project_id, "stage5")

    # 音频列表
    音频_html = '<div class="result-panel"><div class="panel-header"><div class="panel-title">🔊 音频</div>'
    if result.audio_assets:
        音频_html += f'<div class="panel-badge">BGM 情绪：{result.audio_assets.bgm_mood}</div></div>'
        音频_html += '<table style="width:100%;font-size:13px;border-collapse:collapse;">'
        音频_html += '<tr style="text-align:left;color:#6b7280;border-bottom:1px solid #e5e7eb;"><th style="padding:8px">场景</th><th>角色声音</th><th>试听</th></tr>'
        for sa in result.audio_assets.scenes:
            角色行 = ", ".join(f"{c} → {v}" for c, v in sa.character_voice_map.items())
            试听 = ""
            if sa.tts_audio_path and Path(sa.tts_audio_path).exists():
                试听 = f'<audio controls preload="metadata" style="height:30px" src="file={sa.tts_audio_path}"></audio>'
            音频_html += f'<tr style="border-bottom:1px solid #f3f4f6"><td style="padding:8px">场景 {sa.scene_id}</td><td>{角色行}</td><td>{试听}</td></tr>'
        音频_html += '</table>'
    else:
        音频_html += '<div class="empty-state">尚无音频</div>'
    音频_html += '</div>'

    log_text = "\n".join(result.execution_log)
    stages_done = ["stage1", "stage2", "stage3", "stage4", "stage5"]

    yield (
        "### ✅ 第 5 步完成 — 音频已生成",
        音频_html,
        gr.update(value=result),
        log_text,
        stages_done,
        result.project_id,
    )


async def 执行第6步(state_json: str):
    """第6步：合成最终视频。"""
    if not state_json:
        yield "### ⚠️ 请先完成前序步骤", "", gr.update(), "", "", "", None
        return

    state = _state_from_json(state_json)
    state.log("🎞️ 第 6 步：合成最终视频")

    try:
        result = await orchestrator.run_stage(6, state)
    except Exception as e:
        yield f"### ❌ 第 6 步失败：{e}", "", gr.update(), "", "", "", None
        return

    project_store.set_final_video(result.project_id, result.output_video_path)
    project_store.add_stage(result.project_id, "stage6")
    project_store.update_status(result.project_id, "已完成")

    final_video = result.output_video_path if Path(result.output_video_path).exists() else None
    log_text = "\n".join(result.execution_log)
    stages_done = ["stage1", "stage2", "stage3", "stage4", "stage5", "stage6"]

    总结 = f"""### 🎉 视频短剧生成完成！

**输出路径**：`{result.output_video_path}`
**总成本**：${result.total_cost:.4f}
"""

    yield (
        "### 🎉 全部完成 — 最终视频已就绪",
        总结,
        gr.update(value=result),
        log_text,
        result.project_id,
        "",
        final_video,
    )


async def 一键运行全部(user_input: str, api_choice: str, aspect_ratio: str):
    """自动按顺序执行全部 6 个步骤。"""
    if not user_input.strip():
        yield ("### ⚠️ 请先输入故事内容", "", "", "", "", "", "", gr.update(), [], "", "", None)
        return

    project_id = uuid.uuid4().hex[:12]
    project_store.create_project(
        title=user_input[:30] + ("…" if len(user_input) > 30 else ""),
        user_input=user_input,
    )
    project_store.update_status(project_id, "生成中")

    state = DramaState(user_input=user_input, project_id=project_id)
    state.log("🚀 一键运行全部 6 个步骤")

    # 第 1 步
    state.log("━━━ 第 1 步：编剧与视觉设计 ━━━")
    state = await orchestrator.run_stage(1, state)
    if state.error_message:
        yield (f"### ❌ 第 1 步失败：{state.error_message}", "", "", "", "", "", "",
               gr.update(), [], "", "", None)
        return
    project_store.add_stage(project_id, "stage1")
    剧本_md = _剧本摘要(state)
    yield ("### 🟡 进度：1/6 — 剧本完成", 剧本_md, "", "", "", "", "",
           gr.update(value=state), ["stage1"], project_id, "", None)

    # 第 2 步
    state.log("━━━ 第 2 步：角色形象与场景图 ━━━")
    state = await orchestrator.run_stage(2, state)
    project_store.add_stage(project_id, "stage2")
    角色_html, 场景_html = _图片摘要(state)
    yield ("### 🟡 进度：2/6 — 角色与场景图完成", 剧本_md, 角色_html, 场景_html, "", "", "",
           gr.update(value=state), ["stage1", "stage2"], project_id, "", None)

    # 第 3 步
    state.log("━━━ 第 3 步：生成视频提示词 ━━━")
    state = await orchestrator.run_stage(3, state)
    project_store.add_stage(project_id, "stage3")
    prompt_edits = {}
    if state.generation_prompts:
        for i, shot in enumerate(state.generation_prompts.shots):
            prompt_edits[str(i)] = shot.prompt
    yield ("### 🟡 进度：3/6 — 提示词完成", 剧本_md, 角色_html, 场景_html,
           json.dumps(prompt_edits, ensure_ascii=False), "", "",
           gr.update(value=state), ["stage1", "stage2", "stage3"], project_id, "", None)

    # 第 4 步
    state.log("━━━ 第 4 步：生成视频片段 ━━━")
    state = await orchestrator.run_stage(4, state)
    project_store.add_stage(project_id, "stage4")
    视频_html = _视频摘要(state)
    yield ("### 🟡 进度：4/6 — 视频片段完成", 剧本_md, 角色_html, 场景_html,
           json.dumps(prompt_edits, ensure_ascii=False), 视频_html, "",
           gr.update(value=state), ["stage1", "stage2", "stage3", "stage4"], project_id, "", None)

    # 第 5 步
    state.log("━━━ 第 5 步：生成音频 ━━━")
    state = await orchestrator.run_stage(5, state)
    project_store.add_stage(project_id, "stage5")
    音频_html = _音频摘要(state)
    yield ("### 🟡 进度：5/6 — 音频完成", 剧本_md, 角色_html, 场景_html,
           json.dumps(prompt_edits, ensure_ascii=False), 视频_html, 音频_html,
           gr.update(value=state), ["stage1", "stage2", "stage3", "stage4", "stage5"], project_id, "", None)

    # 第 6 步
    state.log("━━━ 第 6 步：合成最终视频 ━━━")
    state = await orchestrator.run_stage(6, state)
    project_store.set_final_video(project_id, state.output_video_path)
    project_store.update_status(project_id, "已完成")
    project_store.add_stage(project_id, "stage6")

    final_video = state.output_video_path if Path(state.output_video_path).exists() else None
    final_md = f"### 🎉 完成！\n\n**输出**：`{state.output_video_path}`\n**总成本**：${state.total_cost:.4f}"

    yield ("### ✅ 全部完成 — 视频已就绪", 剧本_md, 角色_html, 场景_html,
           json.dumps(prompt_edits, ensure_ascii=False), 视频_html, 音频_html,
           gr.update(value=state),
           ["stage1", "stage2", "stage3", "stage4", "stage5", "stage6"],
           project_id, final_md, final_video)


# ═══════════════════════════════════════════════════════════════
#  摘要构建辅助
# ═══════════════════════════════════════════════════════════════

def _剧本摘要(state: DramaState) -> str:
    """构建剧本摘要 — 横向分镜卡片墙。"""
    s = state.script
    if not s:
        return '<div class="empty-state"><div class="icon">🎬</div>尚无剧本</div>'

    分镜卡 = []
    for sc in s.scenes:
        台词预览 = ""
        if sc.dialogue:
            台词预览 = sc.dialogue[0].line
        分镜卡.append(
            f'<div class="scene-card">'
            f'<div class="scene-num">SCENE {sc.scene_id:02d}</div>'
            f'<div class="scene-loc">{sc.location}</div>'
            f'<div class="scene-meta">'
            f'<span class="scene-emotion">{sc.emotion_tone}</span>'
            f'<span>· {sc.duration_sec}s</span>'
            f'<span>· {sc.camera_direction}</span>'
            f'</div>'
            f'<div class="scene-line">"{台词预览}"</div>'
            f'</div>'
        )

    return f"""
<div style="background:white;padding:16px 20px;border-radius:12px;border:1px solid #eef0f3;margin-bottom:12px">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px">
    <h3 style="margin:0;font-size:18px;font-weight:700;color:#1f2937">📜 {s.title}</h3>
    <span class="status-badge info">{s.genre} · {s.duration_sec}秒</span>
  </div>
  <p style="margin:0 0 12px 0;color:#6b7280;font-size:13px;font-style:italic">"{s.logline}"</p>
  <div style="font-size:11px;color:#9ca3af">风格：<b style="color:#6366f1">{state.visual_design.overall_style}</b> · 色调：<b style="color:#6366f1">{state.visual_design.color_palette}</b></div>
</div>
<div class="scene-strip">
{''.join(分镜卡)}
</div>
"""


def _图片摘要(state: DramaState) -> tuple[str, str]:
    """构建角色/场景图摘要 — 9:16 竖屏卡片墙。"""
    角色 = '<div class="portrait-grid">'
    if state.character_images and state.character_images.characters:
        for img in state.character_images.characters:
            路径 = img.image_path
            if 路径 and Path(路径).exists():
                角色 += (
                    f'<div class="portrait-card">'
                    f'<img src="file={路径}" loading="lazy"/>'
                    f'<div class="portrait-overlay">'
                    f'<div class="name">{img.character_name}</div>'
                    f'<div class="meta">角色形象</div>'
                    f'</div>'
                    f'<div class="portrait-badge">9:16</div>'
                    f'</div>'
                )
            else:
                角色 += f'<div class="portrait-empty">⚠️<br>{img.character_name}</div>'
    else:
        角色 += '<div class="empty-state" style="grid-column:1/-1"><div class="icon">👤</div>尚无角色图</div>'
    角色 += '</div>'

    场景 = '<div class="portrait-grid">'
    if state.scene_images and state.scene_images.scenes:
        for img in state.scene_images.scenes:
            路径 = img.image_path
            if 路径 and Path(路径).exists():
                场景 += (
                    f'<div class="portrait-card">'
                    f'<img src="file={路径}" loading="lazy"/>'
                    f'<div class="portrait-overlay">'
                    f'<div class="name">场景 {img.scene_id}</div>'
                    f'<div class="meta">{img.location[:16]}</div>'
                    f'</div>'
                    f'<div class="portrait-badge">9:16</div>'
                    f'</div>'
                )
            else:
                场景 += f'<div class="portrait-empty">⚠️<br>场景 {img.scene_id}</div>'
    else:
        场景 += '<div class="empty-state" style="grid-column:1/-1"><div class="icon">🏙️</div>尚无场景图</div>'
    场景 += '</div>'

    return 角色, 场景


def _视频摘要(state: DramaState) -> str:
    """构建视频摘要 — 9:16 竖屏视频卡片墙。"""
    if not state.generated_assets:
        return '<div class="empty-state"><div class="icon">🎞️</div>尚无视频</div>'
    成功 = sum(1 for s in state.generated_assets.shots if s.success)
    总数 = len(state.generated_assets.shots)

    html = f"""
<div style="background:white;padding:14px 20px;border-radius:12px;border:1px solid #eef0f3;margin-bottom:14px;display:flex;justify-content:space-between;align-items:center">
  <div>
    <h3 style="margin:0;font-size:16px;font-weight:600">🎬 视频片段</h3>
    <p style="margin:4px 0 0 0;color:#6b7280;font-size:12px">点击任意片段可在新窗口放大播放</p>
  </div>
  <div style="display:flex;gap:8px">
    <span class="status-badge {'success' if 成功 == 总数 else 'warning'}">{成功}/{总数} 成功</span>
    <span class="status-badge info">${state.generated_assets.total_cost:.4f}</span>
  </div>
</div>
<div class="portrait-grid">
"""
    for a in state.generated_assets.shots:
        if a.success and a.local_path and Path(a.local_path).exists():
            html += (
                f'<div class="portrait-card">'
                f'<video controls preload="metadata" src="file={a.local_path}"></video>'
                f'<div class="portrait-overlay">'
                f'<div class="name">场景 {a.scene_id}</div>'
                f'<div class="meta">{a.duration_sec:.1f}s · {a.api_used}</div>'
                f'</div>'
                f'<div class="portrait-badge">9:16</div>'
                f'</div>'
            )
        else:
            html += f'<div class="portrait-empty">❌<br>场景 {a.scene_id}<br><span style="font-size:9px">{(a.error_message or "")[:20]}</span></div>'
    html += '</div>'
    return html


def _音频摘要(state: DramaState) -> str:
    """构建音频摘要 — 角色声音映射表 + 试听。"""
    if not state.audio_assets:
        return '<div class="empty-state"><div class="icon">🎤</div>尚无音频</div>'

    角色声线 = {}
    for sa in state.audio_assets.scenes:
        for c, v in sa.character_voice_map.items():
            角色声线[c] = v

    html = f"""
<div style="background:white;padding:16px 20px;border-radius:12px;border:1px solid #eef0f3;margin-bottom:12px">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
    <h3 style="margin:0;font-size:16px;font-weight:600">🔊 音频与配音</h3>
    <span class="status-badge info">BGM 情绪 · {state.audio_assets.bgm_mood}</span>
  </div>
  <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:10px;margin-bottom:14px">
"""
    for 角色, 声线 in 角色声线.items():
        html += f'<div style="padding:10px 14px;background:#fafbff;border-radius:8px;border:1px solid #e0e7ff"><div style="font-size:12px;color:#9ca3af">角色</div><div style="font-weight:600;color:#1f2937">{角色}</div><div style="font-size:11px;color:#6366f1;font-family:monospace;margin-top:2px">{声线}</div></div>'
    html += '</div><h4 style="margin:0 0 8px 0;font-size:13px;color:#6b7280">场景配音</h4>'

    for sa in state.audio_assets.scenes:
        html += f'<div style="padding:10px 14px;background:#f9fafb;border-radius:8px;margin-bottom:6px;display:flex;align-items:center;justify-content:space-between;gap:10px">'
        html += f'<div style="flex:1"><span class="status-badge muted">场景 {sa.scene_id}</span></div>'
        if sa.tts_audio_path and Path(sa.tts_audio_path).exists():
            html += f'<audio controls preload="metadata" style="height:30px;flex:2" src="file={sa.tts_audio_path}"></audio>'
        html += '</div>'
    html += '</div>'
    return html


# ═══════════════════════════════════════════════════════════════
#  资产库回调
# ═══════════════════════════════════════════════════════════════

def 刷新项目列表():
    """刷新项目列表。"""
    projects = project_store.list_projects()
    if not projects:
        return '<div class="empty-state">暂无历史项目</div>', gr.update(choices=[], value=None)
    choices = [f"{p['title']} · {p['id'][:8]}" for p in projects]
    summary_md = '<div class="result-panel"><div class="panel-header"><div class="panel-title">📚 历史项目</div><div class="panel-badge">共 {} 个</div></div>'.format(len(projects))
    for p in projects:
        状态颜色 = {
            "已完成": "var(--c-success)",
            "生成中": "var(--c-warning)",
            "created": "var(--c-muted)",
        }.get(p.get("status", ""), "var(--c-muted)")
        summary_md += (
            f'<div style="padding:10px;border-bottom:1px solid #f3f4f6;display:flex;justify-content:space-between;align-items:center">'
            f'<div><b>{p["title"]}</b><br><span style="color:#9ca3af;font-size:11px">{p.get("updated_at","")} · {p.get("user_input","")[:60]}</span></div>'
            f'<span style="color:{状态_color};font-size:12px;font-weight:500">{p.get("status","")}</span>'
            f'</div>'
        )
    summary_md += '</div>'
    return summary_md, gr.update(choices=choices, value=choices[0] if choices else None)


def 加载项目资产(project_choice: str):
    """加载选中项目的资产。"""
    if not project_choice:
        return ('<div class="empty-state">请先选择项目</div>',) * 5 + (None,)
    projects = project_store.list_projects()
    proj = next((p for p in projects if f"{p['title']} · {p['id'][:8]}" == project_choice), None)
    if not proj:
        return ('<div class="empty-state">项目未找到</div>',) * 5 + (None,)
    pid = proj["id"]
    meta = project_store.get_project(pid)
    if not meta:
        return ('<div class="empty-state">项目元数据丢失</div>',) * 5 + (None,)

    # 剧本
    剧本 = '<div class="empty-state">未生成剧本</div>'
    if meta.get("assets", {}).get("script"):
        s = meta["assets"]["script"]
        剧本 = f'<div class="result-panel"><div class="panel-header"><div class="panel-title">📜 {s.get("title","")}</div></div><blockquote>{s.get("logline","")}</blockquote></div>'

    # 角色
    char_files = project_store.get_asset_files(pid, "characters")
    if char_files:
        角色 = '<div class="result-panel"><div class="panel-header"><div class="panel-title">🎭 角色</div><div class="panel-badge">{} 个</div></div><div class="asset-grid">'.format(len(char_files))
        for f in char_files:
            角色 += f'<div class="asset-card"><img class="asset-thumb" src="file={f}"/><div class="asset-meta">{Path(f).stem}</div></div>'
        角色 += '</div></div>'
    else:
        角色 = '<div class="empty-state">未生成角色图</div>'

    # 场景
    scene_files = project_store.get_asset_files(pid, "scenes")
    if scene_files:
        场景 = '<div class="result-panel"><div class="panel-header"><div class="panel-title">🖼️ 场景</div><div class="panel-badge">{} 个</div></div><div class="asset-grid">'.format(len(scene_files))
        for f in scene_files:
            场景 += f'<div class="asset-card"><img class="asset-thumb" src="file={f}"/><div class="asset-meta">{Path(f).stem}</div></div>'
        场景 += '</div></div>'
    else:
        场景 = '<div class="empty-state">未生成场景图</div>'

    # 视频片段
    video_files = project_store.get_asset_files(pid, "videos")
    if video_files:
        视频 = '<div class="result-panel"><div class="panel-header"><div class="panel-title">🎬 视频片段</div><div class="panel-badge">{} 个</div></div><div class="asset-grid">'.format(len(video_files))
        for f in video_files:
            if f.endswith(".mp4"):
                视频 += f'<div class="asset-card"><video class="asset-thumb" controls preload="metadata" src="file={f}"></video><div class="asset-meta">{Path(f).stem}</div></div>'
        视频 += '</div></div>'
    else:
        视频 = '<div class="empty-state">未生成视频片段</div>'

    # 信息
    信息 = f"""<div class="result-panel">
<div class="panel-header"><div class="panel-title">📋 项目信息</div></div>
<table style="font-size:13px;width:100%">
<tr><td style="padding:6px;color:#6b7280">项目 ID</td><td><code>{pid}</code></td></tr>
<tr><td style="padding:6px;color:#6b7280">创建时间</td><td>{meta.get("created_at","")}</td></tr>
<tr><td style="padding:6px;color:#6b7280">更新时间</td><td>{meta.get("updated_at","")}</td></tr>
<tr><td style="padding:6px;color:#6b7280">状态</td><td>{meta.get("status","")}</td></tr>
<tr><td style="padding:6px;color:#6b7280">已完成阶段</td><td>{', '.join(meta.get("stages_completed",[]))}</td></tr>
</table>
</div>"""

    # 最终视频
    final = meta.get("assets", {}).get("final_video")
    if not final or not Path(final).exists():
        final_files = project_store.get_asset_files(pid, "final")
        final = final_files[0] if final_files else None

    return 剧本, 角色, 场景, 视频, 信息, final


def 删除选中项目(project_choice: str):
    """删除选中的项目。"""
    if not project_choice:
        return "### ⚠️ 未选择项目", gr.update(), '<div class="empty-state">请刷新项目列表</div>'
    projects = project_store.list_projects()
    for p in projects:
        if f"{p['title']} · {p['id'][:8]}" == project_choice:
            project_store.delete_project(p["id"])
            return "### ✅ 已删除项目", gr.update(choices=[], value=None), '<div class="empty-state">项目已删除</div>'
    return "### ❌ 项目未找到", gr.update(), '<div class="empty-state">请刷新项目列表</div>'


# ═══════════════════════════════════════════════════════════════
#  状态序列化与反序列化
# ═══════════════════════════════════════════════════════════════

def _state_from_json(json_str: str) -> DramaState:
    """从 JSON 字符串重建 DramaState。"""
    if not json_str:
        return DramaState()
    try:
        data = json.loads(json_str)
    except (json.JSONDecodeError, TypeError):
        return DramaState()

    state = DramaState()
    state.user_input = data.get("user_input", "")
    state.project_id = data.get("project_id", "")
    state.total_cost = data.get("total_cost", 0.0)
    state.review_loop_count = data.get("review_loop_count", 0)
    state.error_message = data.get("error_message", "")
    state.execution_log = data.get("execution_log", [])
    state.prompt_overrides = data.get("prompt_overrides", {})
    state.user_feedback = data.get("user_feedback", "")
    state.output_video_path = data.get("output_video_path", "")

    # 重建 Pydantic 模型
    from src.schemas.script import ScriptBreakdown
    from src.schemas.visual import VisualDesign
    from src.schemas.character import CharacterImageAssets
    from src.schemas.scene_image import SceneImageAssets
    from src.schemas.prompt import GenerationPrompts
    from src.schemas.assets import GeneratedAssets
    from src.schemas.audio import AudioAssets

    for attr, schema in [
        ("script", ScriptBreakdown),
        ("visual_design", VisualDesign),
        ("character_images", CharacterImageAssets),
        ("scene_images", SceneImageAssets),
        ("generation_prompts", GenerationPrompts),
        ("generated_assets", GeneratedAssets),
        ("audio_assets", AudioAssets),
    ]:
        if data.get(attr):
            try:
                setattr(state, attr, schema.model_validate(data[attr]))
            except Exception:
                pass
    return state


# ═══════════════════════════════════════════════════════════════
#  构建 UI
# ═══════════════════════════════════════════════════════════════

with gr.Blocks(title="🎬 多智能体视频短剧生成器") as demo:
    # 隐藏状态
    current_state = gr.State(None)
    project_id_state = gr.State("")
    stages_done_state = gr.State([])
    prompt_edits_state = gr.State("{}")

    with gr.Tabs():
        # ───────────────────────────────────────────────────────
        # Tab 1: 生成工作台
        # ───────────────────────────────────────────────────────
        with gr.Tab("🎬 生成工作台"):
            # 紧凑品牌头
            gr.HTML("""
            <div class="app-header">
              <h1>🎬 短剧智能工作室</h1>
              <div class="stat-row">
                <div class="stat">📍 阶段 <b>待开始</b></div>
                <div class="stat">⏱ 预估 <b>3-5分钟</b></div>
                <div class="stat">💰 成本 <b>$0-2</b></div>
              </div>
            </div>
            """)

            # ═══ 横向时间轴（紧凑） ═══
            timeline_html = gr.HTML("""
            <div class="timeline-wrap">
              <div class="timeline-header">
                <div class="timeline-title">流水线进度</div>
                <div class="timeline-progress"><b>0</b> / 6</div>
              </div>
              <div class="timeline">
                <div class="timeline-step active" data-stage="1">
                  <div class="dot">1</div>
                  <div class="label">剧本</div>
                </div>
                <div class="timeline-step locked" data-stage="2">
                  <div class="dot">2</div>
                  <div class="label">角色场景</div>
                </div>
                <div class="timeline-step locked" data-stage="3">
                  <div class="dot">3</div>
                  <div class="label">提示词</div>
                </div>
                <div class="timeline-step locked" data-stage="4">
                  <div class="dot">4</div>
                  <div class="label">视频</div>
                </div>
                <div class="timeline-step locked" data-stage="5">
                  <div class="dot">5</div>
                  <div class="label">音频</div>
                </div>
                <div class="timeline-step locked" data-stage="6">
                  <div class="dot">6</div>
                  <div class="label">合成</div>
                </div>
              </div>
            </div>
            """)

            # ═══ 故事输入卡片（沉浸式，暖色背景） ═══
            with gr.Group(elem_classes=["story-input-card"]):
                gr.HTML("""
                <h2>✨ 输入故事创意</h2>
                <div class="hint">AI 将自动完成编剧 → 画图 → 视频 → 配音 → 合成全流程</div>
                """)
                with gr.Row():
                    input_text = gr.Textbox(
                        label="",
                        placeholder="例如：一个穿越到古代的程序员，用代码思维破解了宫斗迷局……",
                        lines=3,
                        scale=4,
                    )
                    with gr.Column(scale=1, min_width=200):
                        api_choice = gr.Dropdown(
                            choices=["mock", "kling"], value="mock",
                            label="视频 API", info="mock 免费"
                        )
                        aspect_ratio = gr.Radio(
                            choices=["9:16", "16:9"], value="9:16",
                            label="画幅比"
                        )
                # 主操作 + 单步操作
                with gr.Row():
                    run_all_btn = gr.Button(
                        "🚀 一键运行全部（自动跑完 6 步）",
                        variant="primary",
                        size="lg",
                        scale=3,
                        elem_classes=["cta-btn"],
                    )
                with gr.Row():
                    btn_s1 = gr.Button("1️⃣ 仅第 1 步", size="sm", scale=1, elem_classes=["step-btn"])
                    btn_s2 = gr.Button("2️⃣ 仅第 2 步", size="sm", scale=1, elem_classes=["step-btn"])
                    btn_s3 = gr.Button("3️⃣ 仅第 3 步", size="sm", scale=1, elem_classes=["step-btn"])
                    btn_s4 = gr.Button("4️⃣ 仅第 4 步", size="sm", scale=1, elem_classes=["step-btn"])
                    btn_s5 = gr.Button("5️⃣ 仅第 5 步", size="sm", scale=1, elem_classes=["step-btn"])
                    btn_s6 = gr.Button("6️⃣ 仅第 6 步", size="sm", scale=1, elem_classes=["step-btn"])

            # ═══ 主内容墙（短剧核心：9:16 卡片墙 + 分镜条） ═══
            with gr.Group(elem_classes=["content-wall"]):
                gr.HTML("""
                <div class="wall-header">
                  <div class="wall-title">
                    <div class="icon">🎬</div>
                    <span>短剧内容</span>
                  </div>
                  <div class="status-badge info">实时同步</div>
                </div>
                """)
                with gr.Tabs():
                    with gr.Tab("📜 剧本分镜"):
                        script_result = gr.HTML('<div class="empty-state"><div class="icon">🎬</div>第 1 步完成后展示分镜卡片<br>短剧将以 9:16 卡片墙形式呈现</div>')
                    with gr.Tab("🎭 角色形象"):
                        char_images_html = gr.HTML('<div class="empty-state"><div class="icon">👤</div>第 2 步完成后展示角色头像</div>')
                    with gr.Tab("🖼️ 场景环境"):
                        scene_images_html = gr.HTML('<div class="empty-state"><div class="icon">🏙️</div>第 2 步完成后展示场景</div>')
                    with gr.Tab("💬 视频提示词"):
                        with gr.Row():
                            with gr.Column(scale=2):
                                prompts_html = gr.HTML('<div class="empty-state"><div class="icon">✍️</div>第 3 步完成后展示提示词卡片</div>')
                            with gr.Column(scale=1):
                                gr.Markdown("**✏️ 编辑提示词**")
                                shot_index = gr.Dropdown(
                                    label="选择镜头",
                                    choices=[str(i) for i in range(20)],
                                    value="0",
                                )
                                prompt_edit_box = gr.Textbox(
                                    label="提示词内容",
                                    lines=6,
                                    placeholder="选择镜头后可在此修改…",
                                    elem_classes=["prompt-edit"],
                                )
                                feedback_input = gr.Textbox(
                                    label="给 AI 的建议",
                                    placeholder="例如：把主角的衣服改成红色",
                                    lines=2,
                                )
                                revise_btn = gr.Button("🤖 AI 修改", variant="secondary")
                                revise_result = gr.Markdown("")
                    with gr.Tab("🎬 视频片段"):
                        videos_html = gr.HTML('<div class="empty-state"><div class="icon">🎞️</div>第 4 步完成后展示视频片段</div>')
                    with gr.Tab("🔊 音频"):
                        audio_html = gr.HTML('<div class="empty-state"><div class="icon">🎤</div>第 5 步完成后展示音频</div>')
                    with gr.Tab("🎞️ 最终视频"):
                        # 沉浸式播放器
                        gr.HTML("""
                        <div class="immersive-player">
                          <div class="player-fallback">📺 第 6 步完成后<br>短剧将在此沉浸式播放</div>
                        </div>
                        """)
                        final_video = gr.Video(label="最终视频", height=480, autoplay=True, visible=False)
                        final_md = gr.Markdown('<div class="empty-state">第 6 步完成后展示最终视频</div>')

            # 状态条 + 日志
            status_md = gr.Markdown("")
            with gr.Accordion("📋 执行日志", open=False):
                log_output = gr.Textbox(label="", lines=10, interactive=False, elem_classes=["log-box"])

            # 示例
            gr.HTML('<div style="margin-top:14px;font-size:12px;color:#9ca3af">💡 <b>试试这些例子</b></div>')
            with gr.Row():
                ex1 = gr.Button("🏢 都市逆袭：被开除员工获预知能力", size="sm")
                ex2 = gr.Button("🔍 悬疑反转：深夜便利店诡异事件", size="sm")
                ex3 = gr.Button("💕 穿越甜宠：程序员变冷宫弃妃", size="sm")

            # 按钮绑定
            btn_s1.click(
                fn=执行第1步,
                inputs=[input_text, api_choice, aspect_ratio],
                outputs=[status_md, script_result, char_images_html,
                         current_state, log_output, stages_done_state, project_id_state],
            )
            btn_s2.click(
                fn=执行第2步,
                inputs=[current_state],
                outputs=[status_md, char_images_html, scene_images_html,
                         current_state, log_output, stages_done_state, project_id_state],
            )
            btn_s3.click(
                fn=执行第3步,
                inputs=[current_state, feedback_input],
                outputs=[status_md, prompt_edits_state,
                         current_state, prompts_html, log_output, stages_done_state, project_id_state],
            )
            btn_s4.click(
                fn=执行第4步,
                inputs=[current_state, prompt_edits_state],
                outputs=[status_md, videos_html, current_state,
                         log_output, stages_done_state, project_id_state, final_video],
            )
            btn_s5.click(
                fn=执行第5步,
                inputs=[current_state],
                outputs=[status_md, audio_html, current_state,
                         log_output, stages_done_state, project_id_state],
            )
            btn_s6.click(
                fn=执行第6步,
                inputs=[current_state],
                outputs=[status_md, final_md, current_state,
                         log_output, project_id_state, stages_done_state, final_video],
            )

            # 一键运行
            run_all_btn.click(
                fn=一键运行全部,
                inputs=[input_text, api_choice, aspect_ratio],
                outputs=[
                    status_md, script_result, char_images_html, scene_images_html,
                    prompt_edits_state, videos_html, audio_html,
                    current_state, stages_done_state, project_id_state, final_md, final_video,
                ],
            )

            # AI 修改提示词
            revise_btn.click(
                fn=AI修改提示词,
                inputs=[current_state, prompt_edits_state, shot_index, feedback_input],
                outputs=[revise_result, prompt_edits_state],
            )

            # 镜头切换时加载提示词
            def 加载镜头提示词(edits_json, idx):
                try:
                    edits = json.loads(edits_json) if edits_json else {}
                except (json.JSONDecodeError, TypeError):
                    edits = {}
                return edits.get(str(idx), "")

            shot_index.change(
                fn=加载镜头提示词,
                inputs=[prompt_edits_state, shot_index],
                outputs=[prompt_edit_box],
            )

            # 编辑框变更时保存
            def 保存镜头编辑(edits_json, idx, text):
                try:
                    edits = json.loads(edits_json) if edits_json else {}
                except (json.JSONDecodeError, TypeError):
                    edits = {}
                edits[str(idx)] = text
                return json.dumps(edits, ensure_ascii=False)

            prompt_edit_box.change(
                fn=保存镜头编辑,
                inputs=[prompt_edits_state, shot_index, prompt_edit_box],
                outputs=[prompt_edits_state],
            )

            # 示例
            ex1.click(fn=lambda: "一个被公司开除的普通员工，意外获得预知未来 5 分钟的能力，在第二天面试中逆袭成为 CEO", outputs=[input_text])
            ex2.click(fn=lambda: "深夜便利店里，收银员发现每个顾客都在重复同一句话：'你已经死了'。查看监控回放后，他崩溃了——因为那些人根本没说过话", outputs=[input_text])
            ex3.click(fn=lambda: "社畜女程序员加班猝死后，穿越成冷宫弃妃。她用现代管理思维整顿后宫，发明了 KPI 考核制度。皇帝反而爱上了这个与众不同的女人", outputs=[input_text])

        # ───────────────────────────────────────────────────────
        # Tab 2: 资产库
        # ───────────────────────────────────────────────────────
        with gr.Tab("📦 资产库"):
            gr.HTML("""
            <div style="background:white;padding:20px;border-radius:10px;border:1px solid #e5e7eb;margin-bottom:12px">
              <h2 style="margin:0;font-size:18px">📚 历史项目资产</h2>
              <p style="margin:6px 0 0 0;color:#6b7280;font-size:13px">浏览所有已生成的短剧项目，包含剧本、角色、场景、视频、音频等资产</p>
            </div>
            """)

            with gr.Row():
                refresh_btn = gr.Button("🔄 刷新项目列表", variant="secondary")
                delete_btn = gr.Button("🗑️ 删除选中项目", variant="stop")

            project_list = gr.Dropdown(label="选择项目", choices=[], interactive=True)
            projects_summary = gr.HTML('<div class="empty-state">点击"刷新项目列表"加载</div>')

            with gr.Tabs():
                with gr.Tab("📜 剧本"):
                    asset_script = gr.HTML('<div class="empty-state">请先选择项目</div>')
                with gr.Tab("🎭 角色"):
                    asset_chars = gr.HTML('<div class="empty-state">请先选择项目</div>')
                with gr.Tab("🖼️ 场景"):
                    asset_scenes = gr.HTML('<div class="empty-state">请先选择项目</div>')
                with gr.Tab("🎬 视频片段"):
                    asset_videos = gr.HTML('<div class="empty-state">请先选择项目</div>')
                with gr.Tab("📋 项目信息"):
                    asset_info = gr.HTML('<div class="empty-state">请先选择项目</div>')
                with gr.Tab("🎞️ 最终视频"):
                    asset_final = gr.Video(label="最终视频", height=480)

            delete_status = gr.Markdown("")

            refresh_btn.click(fn=刷新项目列表, outputs=[projects_summary, project_list])
            project_list.change(
                fn=加载项目资产,
                inputs=[project_list],
                outputs=[asset_script, asset_chars, asset_scenes, asset_videos, asset_info, asset_final],
            )
            delete_btn.click(
                fn=删除选中项目,
                inputs=[project_list],
                outputs=[delete_status, project_list, projects_summary],
            )

        # ───────────────────────────────────────────────────────
        # Tab 3: 设置
        # ───────────────────────────────────────────────────────
        with gr.Tab("⚙️ 系统设置"):
            from src import config as cfg
            import os as _os_settings

            # ── 配置文件读写 ──
            SETTINGS_FILE = cfg.PROJECT_ROOT / "settings_ui.json"

            def _load_saved_profiles():
                """加载已保存的 API 配置档案。"""
                if SETTINGS_FILE.exists():
                    try:
                        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
                    except Exception:
                        pass
                return {"profiles": {}, "active_profile": ""}

            def _save_profiles(data):
                SETTINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

            def _write_env(llm_p, llm_m, llm_k, llm_b, k_ak, k_sk):
                """将 API 配置写入 .env 文件。"""
                env_path = cfg.PROJECT_ROOT / ".env"
                lines = []
                if env_path.exists():
                    lines = env_path.read_text(encoding="utf-8").split("\n")
                # 替换或追加
                to_set = {
                    "LLM_PROVIDER": llm_p,
                    "LLM_MODEL": llm_m,
                    "ANTHROPIC_API_KEY": llm_k if llm_p == "anthropic" else "",
                    "OPENAI_API_KEY": llm_k if llm_p == "openai" else "",
                    "OPENAI_BASE_URL": llm_b,
                    "KLING_ACCESS_KEY": k_ak,
                    "KLING_SECRET_KEY": k_sk,
                }
                new_lines = []
                seen = set()
                for line in lines:
                    key = line.split("=")[0].strip() if "=" in line and not line.startswith("#") else ""
                    if key in to_set:
                        new_lines.append(f"{key}={to_set[key]}")
                        seen.add(key)
                    else:
                        new_lines.append(line)
                for key, val in to_set.items():
                    if key not in seen and val:
                        new_lines.append(f"{key}={val}")
                env_path.write_text("\n".join(new_lines), encoding="utf-8")

            def 保存API配置(profile_name, llm_provider, llm_model, llm_key, llm_base,
                        kling_ak, kling_sk, kling_model, kling_mode,
                        img_api, img_model, current_profiles_json):
                """保存当前填入的 API 配置为命名档案。"""
                try:
                    saved = json.loads(current_profiles_json) if current_profiles_json else {"profiles": {}, "active_profile": ""}
                except Exception:
                    saved = {"profiles": {}, "active_profile": ""}
                saved["profiles"][profile_name] = {
                    "llm_provider": llm_provider,
                    "llm_model": llm_model,
                    "llm_key": llm_key,
                    "llm_base_url": llm_base,
                    "kling_access_key": kling_ak,
                    "kling_secret_key": kling_sk,
                    "kling_model": kling_model,
                    "kling_mode": kling_mode,
                    "image_gen_api": img_api,
                    "image_gen_model": img_model,
                }
                saved["active_profile"] = profile_name
                _save_profiles(saved)
                # 同时更新当前 .env
                _write_env(llm_provider, llm_model, llm_key, llm_base, kling_ak, kling_sk)
                档案列表 = list(saved["profiles"].keys())
                return json.dumps(saved, ensure_ascii=False), f"✅ 已保存档案「{profile_name}」并写入 .env，重启生效", gr.update(choices=档案列表, value=profile_name)

            def 加载档案(profile_name, current_profiles_json):
                """加载指定档案到编辑框。"""
                try:
                    saved = json.loads(current_profiles_json) if current_profiles_json else {}
                except Exception:
                    saved = {}
                p = saved.get("profiles", {}).get(profile_name, {})
                return (
                    p.get("llm_provider", ""),
                    p.get("llm_model", ""),
                    p.get("llm_key", ""),
                    p.get("llm_base_url", ""),
                    p.get("kling_access_key", ""),
                    p.get("kling_secret_key", ""),
                    p.get("kling_model", "kling-v2-6"),
                    p.get("kling_mode", "std"),
                    p.get("image_gen_api", "mock"),
                    p.get("image_gen_model", ""),
                )

            def 删除档案(profile_name, current_profiles_json):
                try:
                    saved = json.loads(current_profiles_json) if current_profiles_json else {}
                except Exception:
                    saved = {}
                saved.get("profiles", {}).pop(profile_name, None)
                if saved.get("active_profile") == profile_name:
                    saved["active_profile"] = ""
                _save_profiles(saved)
                档案列表 = list(saved["profiles"].keys())
                return json.dumps(saved, ensure_ascii=False), f"✅ 已删除「{profile_name}」", gr.update(choices=档案列表, value=None)

            # 加载现有档案
            _saved = _load_saved_profiles()
            _profile_names = list(_saved.get("profiles", {}).keys())
            _active = _saved.get("active_profile", "")
            _profiles_str = gr.State(json.dumps(_saved, ensure_ascii=False))

            # ── UI ──
            gr.Markdown("### ⚙️ API 与模型配置")
            gr.Markdown("*配置保存后写入 `.env` 文件，需重启应用生效。支持保存多组配置随时切换。*")

            with gr.Row():
                档案选择 = gr.Dropdown(label="已保存的配置档案", choices=_profile_names, value=_active or None, scale=3)
                档案名称 = gr.Textbox(label="新档案名称", placeholder="例如：我的Claude、公司DeepSeek、备用Kling", scale=2)
                with gr.Column(scale=1, min_width=80):
                    加载档案_btn = gr.Button("📂 加载", size="sm")
                    删除档案_btn = gr.Button("🗑️ 删除", size="sm")

            with gr.Accordion("🧠 大语言模型", open=True):
                with gr.Row():
                    llm_provider = gr.Dropdown(label="LLM 服务商", choices=["anthropic", "openai"], value=cfg.LLM_PROVIDER)
                    llm_model = gr.Textbox(label="模型名称", value=cfg.LLM_MODEL, placeholder="claude-sonnet-4-6")
                with gr.Row():
                    llm_key = gr.Textbox(label="API Key", value=cfg.ANTHROPIC_API_KEY if cfg.ANTHROPIC_API_KEY != "your-api-key-here" else "", type="password", placeholder="sk-...")
                    llm_base = gr.Textbox(label="Base URL（OpenAI 兼容用）", value=cfg.OPENAI_BASE_URL, placeholder="留空使用默认")

            with gr.Accordion("🎬 视频生成 API", open=False):
                with gr.Row():
                    kling_ak = gr.Textbox(label="Kling Access Key", value=cfg.KLING_ACCESS_KEY, type="password", placeholder="可灵 AK")
                    kling_sk = gr.Textbox(label="Kling Secret Key", value=cfg.KLING_SECRET_KEY, type="password", placeholder="可灵 SK")
                with gr.Row():
                    kling_model = gr.Textbox(label="Kling 模型", value=cfg.KLING_MODEL)
                    kling_mode = gr.Dropdown(label="模式", choices=["std", "pro"], value=cfg.KLING_MODE)

            with gr.Accordion("🖼️ 图片生成 API", open=False):
                with gr.Row():
                    img_api = gr.Dropdown(label="图片 API", choices=["mock", "openai"], value=cfg.IMAGE_GEN_API)
                    img_model = gr.Textbox(label="图片模型", value=cfg.IMAGE_GEN_MODEL)

            with gr.Row():
                保存API_btn = gr.Button("💾 保存当前配置为档案", variant="primary", scale=2)
                保存状态 = gr.Markdown("", scale=3)

            gr.Markdown("---")
            gr.Markdown("### 📊 生成参数（需修改 `.env` 文件生效）")
            gr.HTML(f"""
            <div class="result-panel"><table style="width:100%;font-size:12px">
            <tr><td style="padding:4px;color:#6b7280;width:30%">最大并发</td><td>{cfg.MAX_CONCURRENT_GENERATIONS}</td>
            <td style="padding:4px;color:#6b7280;width:30%">超时</td><td>{cfg.GENERATION_TIMEOUT}s</td></tr>
            <tr><td style="padding:4px;color:#6b7280">审核重试</td><td>{cfg.MAX_REVIEW_LOOPS} 次</td>
            <td style="padding:4px;color:#6b7280">历史项目</td><td>{cfg.MAX_PROJECT_HISTORY} 个</td></tr>
            <tr><td style="padding:4px;color:#6b7280">输出编码</td><td>{cfg.OUTPUT_CODEC} @ {cfg.OUTPUT_FPS}fps @ {cfg.OUTPUT_BITRATE}</td>
            <td style="padding:4px;color:#6b7280">输出目录</td><td><code style="font-size:10px">{cfg.OUTPUT_DIR}</code></td></tr>
            </table></div>
            """)

            # 事件绑定
            保存API_btn.click(
                fn=保存API配置,
                inputs=[档案名称, llm_provider, llm_model, llm_key, llm_base,
                        kling_ak, kling_sk, kling_model, kling_mode,
                        img_api, img_model, _profiles_str],
                outputs=[_profiles_str, 保存状态, 档案选择],
            )
            加载档案_btn.click(
                fn=加载档案,
                inputs=[档案选择, _profiles_str],
                outputs=[llm_provider, llm_model, llm_key, llm_base,
                         kling_ak, kling_sk, kling_model, kling_mode,
                         img_api, img_model],
            )
            删除档案_btn.click(
                fn=删除档案,
                inputs=[档案选择, _profiles_str],
                outputs=[_profiles_str, 保存状态, 档案选择],
            )


# ═══════════════════════════════════════════════════════════════
#  启动
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import socket as _sock
    PORT = 17860
    for _offset in range(10):
        s = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
        try:
            s.bind(('127.0.0.1', PORT + _offset))
            s.close()
            PORT = PORT + _offset
            break
        except OSError:
            s.close()
    else:
        PORT = 17860

    demo.launch(
        server_name="127.0.0.1",
        server_port=PORT,
        share=False,
        show_error=False,
        theme=gr.themes.Soft(),
        css=CUSTOM_CSS,
    )
