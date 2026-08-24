"""Global configuration for the Video Drama Agent system."""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Project paths
PROJECT_ROOT = Path(__file__).parent.parent
OUTPUT_DIR = PROJECT_ROOT / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

# LLM Configuration
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic")  # "anthropic", "openai", "deepseek", "kimi", or "qwen"
LLM_MODEL = os.getenv("LLM_MODEL", "claude-sonnet-4-6")
if os.getenv("LLM_PROVIDER", "anthropic") == "deepseek":
    if LLM_MODEL in {"", "deepseek-chat"}:
        LLM_MODEL = "deepseek-v4-flash"
    elif LLM_MODEL == "deepseek-reasoner":
        LLM_MODEL = "deepseek-v4-pro"
if os.getenv("LLM_PROVIDER", "anthropic") == "kimi" and LLM_MODEL in {
    "", "moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k",
}:
    LLM_MODEL = "kimi-k2.6"
if os.getenv("LLM_PROVIDER", "anthropic") == "qwen" and not os.getenv("LLM_MODEL", "").strip():
    LLM_MODEL = "qwen-plus"
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "")  # For custom endpoints
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "1800"))
LLM_STREAM = os.getenv("LLM_STREAM", "true").strip().lower() not in {"0", "false", "no", "off"}
LLM_REASONING_EFFORT = os.getenv("LLM_REASONING_EFFORT", "high").strip().lower()  # deepseek: low/medium/high/max; 空/off 关闭
# Default max output tokens for OpenAI-compatible structured/text calls that do
# not set their own cap. The storyboard-visualize stage emits large JSON for
# every shot, so the API default is too small and truncates the response.
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "8192"))

# Post-generation video review. Kept separate from the main LLM so Kimi can
# continue handling script parsing while a vision model audits generated clips.
REVIEW_LLM_ENABLED = os.getenv("REVIEW_LLM_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}
REVIEW_LLM_PROVIDER = os.getenv("REVIEW_LLM_PROVIDER", "").strip().lower()
REVIEW_LLM_MODEL = os.getenv("REVIEW_LLM_MODEL", "qwen-vl-max")
_happyhorse_base_url = os.getenv("HAPPYHORSE_BASE_URL", "https://dashscope.aliyuncs.com").rstrip("/").lower()
_happyhorse_is_official_dashscope = _happyhorse_base_url in {
    "https://dashscope.aliyuncs.com",
    "http://dashscope.aliyuncs.com",
}
REVIEW_LLM_API_KEY = (
    os.getenv("REVIEW_LLM_API_KEY", "")
    or (os.getenv("DASHSCOPE_API_KEY", "") if not REVIEW_LLM_PROVIDER else "")
    or (os.getenv("HAPPYHORSE_API_KEY", "") if not REVIEW_LLM_PROVIDER and _happyhorse_is_official_dashscope else "")
)
REVIEW_LLM_BASE_URL = os.getenv("REVIEW_LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
REVIEW_LLM_TIMEOUT = int(os.getenv("REVIEW_LLM_TIMEOUT", "120"))

# Video API Configuration — Kling (可灵)
KLING_ACCESS_KEY = os.getenv("KLING_ACCESS_KEY", "")
KLING_SECRET_KEY = os.getenv("KLING_SECRET_KEY", "")
KLING_API_KEY = os.getenv("KLING_API_KEY", "")       # New-style API key (api-key-kling-...)
KLING_MODEL = os.getenv("KLING_MODEL", "kling-v2-6")
KLING_MODE = os.getenv("KLING_MODE", "std")  # "std" or "pro"
KLING_IMAGE_MODEL = os.getenv("KLING_IMAGE_MODEL", "kling-v1")
# Legacy keys (keep for backward compat)
KLING_API_SECRET = os.getenv("KLING_API_SECRET", "")
RUNWAY_API_KEY = os.getenv("RUNWAY_API_KEY", "")
PIKA_API_KEY = os.getenv("PIKA_API_KEY", "")

# Happy Horse (快马) Video API Configuration
HAPPYHORSE_API_KEY = os.getenv("HAPPYHORSE_API_KEY", "")
HAPPYHORSE_BASE_URL = os.getenv("HAPPYHORSE_BASE_URL", "https://dashscope.aliyuncs.com")
HAPPYHORSE_MODEL = os.getenv("HAPPYHORSE_MODEL", "happyhorse-1.1-r2v")
HAPPYHORSE_RESOLUTION = os.getenv("HAPPYHORSE_RESOLUTION", "1080p")

# Seedance (Volcengine Ark) Video API Configuration
ARK_API_KEY = os.getenv("ARK_API_KEY", "")
ARK_BASE_URL = os.getenv("ARK_BASE_URL", "https://ark.cn-beijing.volces.com")
ARK_MODEL = os.getenv("ARK_MODEL", "doubao-seedance-2-0-fast-260128")
ARK_RESOLUTION = os.getenv("ARK_RESOLUTION", "1080p")

# Default video generation settings
DEFAULT_API = os.getenv("DEFAULT_VIDEO_API", "mock")  # "kling", "runway", "pika", "happyhorse", "seedance", "mock"
DEFAULT_ASPECT_RATIO = "9:16"  # Vertical video for short drama
DEFAULT_DURATION_PER_SHOT = 5  # seconds
MAX_RETRIES = 5
MAX_CONCURRENT_GENERATIONS = 3
GENERATION_TIMEOUT = int(os.getenv("VIDEO_GENERATION_TIMEOUT", "1200"))  # seconds

# Image Generation Configuration
IMAGE_GEN_API = os.getenv("IMAGE_GEN_API", "mock")  # "mock" or "openai"
IMAGE_GEN_MODEL = os.getenv("IMAGE_GEN_MODEL", "dall-e-3")
IMAGE_GEN_KEY = os.getenv("IMAGE_GEN_KEY", "")  # 独立图片 API Key，留空复用 OPENAI_API_KEY
IMAGE_WIDTH = 1080
IMAGE_HEIGHT = 1920
CHARACTER_IMAGE_SIZE = (1024, 1024)
MAX_CONCURRENT_IMAGE_GENERATIONS = 5
IMAGE_GENERATION_TIMEOUT = int(os.getenv("IMAGE_GENERATION_TIMEOUT", "180"))

# Output settings
OUTPUT_FPS = 24
OUTPUT_CODEC = "libx264"
OUTPUT_BITRATE = "5000k"

# Project management
MAX_PROJECT_HISTORY = 50

# LangGraph settings
MAX_REVIEW_LOOPS = 2  # Max review-retry cycles
CHECKPOINT_DB_PATH = str(OUTPUT_DIR / "checkpoints.db")

# Prompt review → revise → re-review closed loop. Cap the number of automatic
# revision rounds so a stubborn issue set cannot loop forever.
MAX_PROMPT_REVISION_ROUNDS = int(os.getenv("MAX_PROMPT_REVISION_ROUNDS", "3"))
