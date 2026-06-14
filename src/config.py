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
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic")  # "anthropic" or "openai"
LLM_MODEL = os.getenv("LLM_MODEL", "claude-sonnet-4-6")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "")  # For custom endpoints

# Video API Configuration — Kling (可灵)
KLING_ACCESS_KEY = os.getenv("KLING_ACCESS_KEY", "")
KLING_SECRET_KEY = os.getenv("KLING_SECRET_KEY", "")
KLING_MODEL = os.getenv("KLING_MODEL", "kling-v2-6")
KLING_MODE = os.getenv("KLING_MODE", "std")  # "std" or "pro"
# Legacy keys (keep for backward compat)
KLING_API_KEY = os.getenv("KLING_API_KEY", "")
KLING_API_SECRET = os.getenv("KLING_API_SECRET", "")
RUNWAY_API_KEY = os.getenv("RUNWAY_API_KEY", "")
PIKA_API_KEY = os.getenv("PIKA_API_KEY", "")

# Default video generation settings
DEFAULT_API = os.getenv("DEFAULT_VIDEO_API", "mock")  # "kling", "runway", "pika", "mock"
DEFAULT_ASPECT_RATIO = "9:16"  # Vertical video for short drama
DEFAULT_DURATION_PER_SHOT = 5  # seconds
MAX_RETRIES = 3
MAX_CONCURRENT_GENERATIONS = 5
GENERATION_TIMEOUT = 300  # seconds

# Image Generation Configuration
IMAGE_GEN_API = os.getenv("IMAGE_GEN_API", "mock")  # "mock" or "openai"
IMAGE_GEN_MODEL = os.getenv("IMAGE_GEN_MODEL", "dall-e-3")
IMAGE_GEN_KEY = os.getenv("IMAGE_GEN_KEY", "")  # 独立图片 API Key，留空复用 OPENAI_API_KEY
IMAGE_WIDTH = 1080
IMAGE_HEIGHT = 1920
CHARACTER_IMAGE_SIZE = (1024, 1024)
MAX_CONCURRENT_IMAGE_GENERATIONS = 5

# Output settings
OUTPUT_FPS = 24
OUTPUT_CODEC = "libx264"
OUTPUT_BITRATE = "5000k"

# Project management
MAX_PROJECT_HISTORY = 50

# LangGraph settings
MAX_REVIEW_LOOPS = 2  # Max review-retry cycles
CHECKPOINT_DB_PATH = str(OUTPUT_DIR / "checkpoints.db")
