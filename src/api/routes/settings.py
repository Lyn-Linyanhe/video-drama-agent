"""FastAPI routes extracted from the original server entrypoint."""

from fastapi import APIRouter
from ..context import *

router = APIRouter()

# ── API 配置档案 ──────────────────────────────────────────────
SETTINGS_FILE = BASE_DIR / "output" / "settings_ui.json"
SECRET_SETTING_FIELDS = (
    "llm_key",
    "kimi_prompt_review_api_key",
    "kling_api_key",
    "kling_access_key",
    "kling_secret_key",
    "happyhorse_api_key",
    "ark_api_key",
    "image_gen_key",
    "review_llm_api_key",
)

# These keys belonged to the old, provider-agnostic audio settings form.  The
# audio pipeline now has one real primary credential: the DashScope credential
# shared with the HappyHorse video account.  Keep accepting old exports, but
# never write these fields back to a profile or environment file.
LEGACY_AUDIO_PROFILE_FIELDS = (
    "tts_provider",
    "tts_key",
    "tts_base_url",
    "tts_model",
    "tts_voice",
    "tts_speed",
    "tts_timeout",
    "bgm_provider",
    "bgm_key",
    "bgm_base_url",
    "bgm_duration",
    "bgm_timeout",
    "minimax_api_key",
)

OPENAI_COMPATIBLE_LLM_PROVIDERS = {"openai", "deepseek", "kimi", "qwen"}
LLM_DEFAULT_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "kimi": "https://api.moonshot.cn/v1",
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
}
LLM_DEFAULT_MODELS = {
    "openai": "gpt-4o-mini",
    "deepseek": "deepseek-v4-flash",
    "kimi": "kimi-k2.6",
    "qwen": "qwen-plus",
}


def _normalize_llm_model(provider: str, model: str) -> str:
    """Keep DeepSeek on the supported Flash/Pro model pair.

    ``deepseek-chat`` was the old project default and ``deepseek-reasoner``
    was the old reasoning alias.  Migrate those persisted names once they are
    read, while leaving explicit Flash/Pro selections unchanged.
    """
    provider_key = str(provider or "").strip().lower()
    model_name = str(model or "").strip()
    if provider_key == "deepseek":
        if model_name in {"", "deepseek-chat"}:
            return "deepseek-v4-flash"
        if model_name == "deepseek-reasoner":
            return "deepseek-v4-pro"
    if provider_key == "kimi" and model_name in {"", "moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"}:
        return LLM_DEFAULT_MODELS["kimi"]
    if provider_key == "qwen" and not model_name:
        return LLM_DEFAULT_MODELS["qwen"]
    return model_name


def _normalize_llm_timeout(value: object) -> str:
    """Keep long script stages from being configured below a safe floor."""
    try:
        seconds = int(str(value or "300").strip())
    except (TypeError, ValueError):
        seconds = 300
    return str(max(300, min(seconds, 7200)))


def _load_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"profiles": {}, "active_profile": ""}

def _save_settings(data: dict):
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _normalize_audio_profile(profile: dict) -> dict:
    """Migrate legacy settings while retaining both DashScope TTS adapters."""
    normalized = dict(profile or {})
    normalized["llm_model"] = _normalize_llm_model(
        normalized.get("llm_provider", ""),
        normalized.get("llm_model", ""),
    )
    normalized["llm_timeout"] = _normalize_llm_timeout(normalized.get("llm_timeout", "300"))
    if str(normalized.get("llm_provider") or "").strip().lower() == "deepseek":
        # DeepSeek requests use one deterministic temperature in the provider
        # layer; keep the persisted setting consistent with the effective value.
        normalized["llm_temperature"] = "0.25"
    normalized.setdefault("kimi_prompt_review_enabled", True)
    normalized.setdefault("kimi_prompt_review_model", "kimi-k2.6")
    normalized.setdefault("kimi_prompt_review_base_url", "https://api.moonshot.cn/v1")
    normalized.setdefault("review_llm_enabled", True)
    normalized.setdefault("review_llm_provider", "")
    normalized.setdefault("review_llm_model", "qwen-vl-max")
    normalized.setdefault("review_llm_api_key", "")
    normalized.setdefault("review_llm_base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    legacy_key = str(normalized.get("minimax_api_key") or "").strip()
    if not normalized.get("happyhorse_api_key") and legacy_key:
        normalized["happyhorse_api_key"] = legacy_key
    for field in LEGACY_AUDIO_PROFILE_FIELDS:
        normalized.pop(field, None)
    normalized.setdefault("dashscope_tts_provider", "qwen3")
    normalized.setdefault("qwen_tts_model", "qwen3-tts-flash")
    normalized.setdefault("qwen_timeout", "45")
    normalized.setdefault("qwen_default_male_voice", "Ethan")
    normalized.setdefault("qwen_default_female_voice", "Cherry")
    return normalized


def _select_effective_profile(data: dict, preferred_name: str = "") -> tuple[str, dict, str]:
    """Keep an incomplete profile from disabling a previously configured LLM."""
    profiles = data.get("profiles") or {}
    requested = preferred_name or str(data.get("active_profile") or "")
    requested_profile = _normalize_audio_profile(profiles.get(requested, {}))
    if requested_profile and str(requested_profile.get("llm_key") or "").strip():
        return requested, requested_profile, ""

    for name, raw_profile in profiles.items():
        candidate = _normalize_audio_profile(raw_profile)
        if str(candidate.get("llm_key") or "").strip():
            return name, candidate, requested
    return requested, requested_profile, ""


def _public_profile(profile: dict) -> dict:
    """Return settings metadata without exposing credentials to the browser."""
    public = _normalize_audio_profile(profile)
    public["_secret_configured"] = {
        field: bool(public.get(field)) for field in SECRET_SETTING_FIELDS
    }
    for field in SECRET_SETTING_FIELDS:
        public[field] = ""
    return public


def _public_settings(data: dict) -> dict:
    return {
        "profiles": {
            name: _public_profile(profile)
            for name, profile in data.get("profiles", {}).items()
        },
        "active_profile": data.get("active_profile", ""),
    }


def _merge_profile_secrets(incoming: dict, existing: dict, clear_fields: list[str]) -> dict:
    clear = set(clear_fields)
    # Clearing a legacy MiniMax key must clear the key it is migrated to too.
    if "minimax_api_key" in clear:
        clear.add("happyhorse_api_key")
    clear &= set(SECRET_SETTING_FIELDS)
    provider_changed = (
        str(incoming.get("llm_provider") or "").strip().lower()
        != str(existing.get("llm_provider") or "").strip().lower()
    )
    for field in SECRET_SETTING_FIELDS:
        if field in clear:
            incoming[field] = ""
        elif field == "llm_key" and provider_changed and not incoming.get(field):
            incoming[field] = ""
        elif not incoming.get(field) and existing.get(field):
            incoming[field] = existing[field]
    return incoming

class SettingsRequest(BaseModel):
    profile_name: str = "默认配置"
    # ── 全局 ──
    http_proxy: str = ""  # HTTP 代理地址
    # ── LLM ──
    llm_provider: str = "anthropic"
    llm_model: str = ""
    llm_key: str = ""
    llm_base_url: str = ""
    llm_timeout: str = "1800"
    llm_stream: str = "true"
    # DeepSeek is the default provider and uses a deterministic effective
    # temperature. Other providers may still ignore this optional setting.
    llm_temperature: str = "0.25"
    llm_top_p: str = "0.9"
    # ── Dedicated Kimi storyboard-prompt review ──
    kimi_prompt_review_enabled: bool = True
    kimi_prompt_review_api_key: str = ""
    kimi_prompt_review_model: str = "kimi-k2.6"
    kimi_prompt_review_base_url: str = "https://api.moonshot.cn/v1"
    # ── Dedicated visual review ──
    review_llm_enabled: bool = True
    review_llm_provider: str = ""
    review_llm_model: str = "qwen-vl-max"
    review_llm_api_key: str = ""
    review_llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    # ── Audio ──
    # Legacy values stay parseable for imported profiles but are excluded from
    # every persisted profile. Both adapters use happyhorse_api_key/base_url.
    tts_provider: str = Field(default="", exclude=True)
    tts_key: str = Field(default="", exclude=True)
    tts_base_url: str = Field(default="", exclude=True)
    tts_model: str = Field(default="", exclude=True)
    tts_voice: str = Field(default="", exclude=True)
    tts_speed: str = Field(default="", exclude=True)
    tts_timeout: str = Field(default="", exclude=True)
    minimax_api_key: str = Field(default="", exclude=True)
    minimax_model: str = "MiniMax/speech-2.8-turbo"
    minimax_timeout: str = "45"
    minimax_default_male_voice: str = ""
    minimax_default_female_voice: str = ""
    dashscope_tts_provider: str = "qwen3"  # qwen3 / minimax
    qwen_tts_model: str = "qwen3-tts-flash"
    qwen_timeout: str = "45"
    qwen_default_male_voice: str = "Ethan"
    qwen_default_female_voice: str = "Cherry"
    edge_tts_fallback: bool = True
    # BGM is supplied by the project audio library, not an online provider.
    bgm_provider: str = Field(default="", exclude=True)
    bgm_key: str = Field(default="", exclude=True)
    bgm_base_url: str = Field(default="", exclude=True)
    bgm_duration: str = Field(default="", exclude=True)
    bgm_timeout: str = Field(default="", exclude=True)
    # ── 视频 ──
    default_video_api: str = "happyhorse"
    video_base_url: str = ""
    kling_api_key: str = ""
    kling_access_key: str = ""
    kling_secret_key: str = ""
    kling_model: str = "kling-v2-6"
    kling_mode: str = "std"
    happyhorse_api_key: str = ""
    happyhorse_base_url: str = "https://dashscope.aliyuncs.com"
    happyhorse_model: str = "happyhorse-1.1-r2v"
    happyhorse_resolution: str = "1080p"
    ark_api_key: str = ""
    ark_base_url: str = "https://ark.cn-beijing.volces.com"
    ark_model: str = "doubao-seedance-2-0-fast-260128"
    ark_resolution: str = "1080p"
    # ── 图片 ──
    image_gen_api: str = "mock"
    image_gen_key: str = ""
    kling_image_model: str = "kling-v1"
    clear_secret_fields: list[str] = Field(default_factory=list)

@router.get("/api/settings")
async def list_settings():
    return _public_settings(_load_settings())


def visual_review_capabilities() -> dict:
    """Expose visual-review readiness without returning any credential."""
    import src.config as config
    from src.services.media_quality import resolve_review_config

    review = resolve_review_config()
    explicit_provider = str(getattr(config, "REVIEW_LLM_PROVIDER", "") or "").strip().lower()
    legacy_inferred = not explicit_provider and review.get("provider") == "qwen"
    base_url = str(review.get("base_url") or "").rstrip("/")
    host = base_url.split("/", 3)[2] if "://" in base_url else base_url
    enabled = bool(getattr(config, "REVIEW_LLM_ENABLED", True))
    configured = bool(enabled and review.get("provider") in {"qwen", "openai"} and review.get("model") and review.get("api_key"))
    return {
        "enabled": enabled,
        "configured": configured,
        "provider": review.get("provider", ""),
        "model": review.get("model", ""),
        "endpoint_host": host,
        "legacy_inferred": legacy_inferred,
        "status": "ok" if configured else ("disabled" if not enabled else "missing_key"),
    }


@router.get("/api/settings/visual-review-capabilities")
async def get_visual_review_capabilities():
    return visual_review_capabilities()


@router.post("/api/settings/test-visual-review")
async def test_visual_review(req: "TestConnectionRequest"):
    """Probe the configured vision chat endpoint without starting generation."""
    import base64
    import httpx
    import src.config as config
    from src.services.media_quality import resolve_review_config

    configured = resolve_review_config()
    provider = str(req.provider or configured.get("provider") or "").strip().lower()
    model = str(req.model or configured.get("model") or "qwen-vl-max").strip()
    key = str(req.key or configured.get("api_key") or "").strip()
    base_url = str(req.base_url or configured.get("base_url") or "").strip().rstrip("/")
    if provider not in {"qwen", "openai"}:
        return {"success": False, "status": 0, "category": "disabled", "detail": "当前没有配置受支持的视觉模型供应商"}
    if not key:
        return {"success": False, "status": 0, "category": "missing_key", "provider": provider, "model": model, "detail": "请先配置视觉模型 API Key"}
    if not base_url:
        return {"success": False, "status": 0, "category": "endpoint", "provider": provider, "model": model, "detail": "请先配置视觉模型端点"}
    # A small valid PNG larger than Qwen-VL's 10px minimum. This is a
    # non-generation probe payload and is never persisted as an asset.
    image_data = base64.b64encode(
        bytes.fromhex("89504e470d0a1a0a0000000d4948445200000010000000100802000000909168360000002249444154789c636c68686020053091a49a61540371808948757030aa811840b20600c94c01a050c670ec0000000049454e44ae426082")
    ).decode("ascii")
    url = f"{base_url}/chat/completions"
    body = {
        "model": model,
        "temperature": 0,
        "max_tokens": 8,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": "只回复 OK，确认你能读取这张图片。"},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_data}"}},
            ],
        }],
    }
    try:
        async with httpx.AsyncClient(timeout=max(10, int(getattr(config, "REVIEW_LLM_TIMEOUT", 120) or 120))) as client:
            response = await client.post(url, json=body, headers={"Authorization": f"Bearer {key}"})
        result = _probe_result(response, url)
        result.update({"provider": provider, "model": model})
        return result
    except Exception as exc:
        result = _connection_failure(exc, url)
        result.update({"provider": provider, "model": model})
        return result

@router.get("/api/settings/{profile_name}")
async def get_setting(profile_name: str):
    data = _load_settings()
    profile = data.get("profiles", {}).get(profile_name)
    if not profile:
        raise HTTPException(404, f"档案 '{profile_name}' 不存在")
    return _public_profile(profile)

@router.post("/api/settings")
async def save_settings(req: SettingsRequest):
    data = _load_settings()
    name = req.profile_name.strip()
    if not name:
        raise HTTPException(400, "配置名称不能为空")
    if len(name) > 80:
        raise HTTPException(400, "配置名称不能超过 80 个字符")
    data.setdefault("profiles", {})
    existing = _normalize_audio_profile(data["profiles"].get(name, {}))
    profile = req.model_dump(exclude={"clear_secret_fields"})
    if not profile.get("happyhorse_api_key") and req.minimax_api_key:
        profile["happyhorse_api_key"] = req.minimax_api_key
    profile["profile_name"] = name
    profile = _merge_profile_secrets(
        profile,
        existing,
        req.clear_secret_fields,
    )
    profile = _normalize_audio_profile(profile)
    data["profiles"][name] = profile
    effective_name, effective_profile, fallback_from = _select_effective_profile(data, name)
    data["active_profile"] = effective_name
    profile = effective_profile
    _save_settings(data)
    _write_env(
        llm_provider=profile["llm_provider"], llm_model=profile["llm_model"],
        llm_key=profile["llm_key"], llm_base=profile["llm_base_url"],
        default_video_api=profile["default_video_api"],
        kling_api_key=profile["kling_api_key"],
        kling_ak=profile["kling_access_key"], kling_sk=profile["kling_secret_key"],
        kling_model=profile["kling_model"], kling_mode=profile["kling_mode"],
        happyhorse_key=profile["happyhorse_api_key"],
        happyhorse_url=profile["happyhorse_base_url"],
        happyhorse_model=profile["happyhorse_model"],
        happyhorse_res=profile["happyhorse_resolution"],
        ark_key=profile["ark_api_key"],
        ark_url=profile["ark_base_url"],
        ark_model=profile["ark_model"],
        ark_resolution=profile["ark_resolution"],
        img_api=profile["image_gen_api"], img_key=profile["image_gen_key"],
        kling_img_model=profile["kling_image_model"],
        http_proxy=profile["http_proxy"], tts_provider=profile["dashscope_tts_provider"],
        qwen_model=profile["qwen_tts_model"], qwen_timeout=profile["qwen_timeout"],
        qwen_male_voice=profile["qwen_default_male_voice"], qwen_female_voice=profile["qwen_default_female_voice"],
        minimax_model=profile["minimax_model"],
        minimax_timeout=profile["minimax_timeout"], edge_tts_fallback=profile["edge_tts_fallback"],
        minimax_male_voice=profile["minimax_default_male_voice"], minimax_female_voice=profile["minimax_default_female_voice"],
        llm_timeout=profile["llm_timeout"], llm_stream=profile["llm_stream"],
        kimi_prompt_review_enabled=profile["kimi_prompt_review_enabled"],
        kimi_prompt_review_key=profile["kimi_prompt_review_api_key"],
        kimi_prompt_review_model=profile["kimi_prompt_review_model"],
        kimi_prompt_review_base_url=profile["kimi_prompt_review_base_url"],
        review_llm_enabled=profile["review_llm_enabled"],
        review_llm_provider=profile["review_llm_provider"],
        review_llm_model=profile["review_llm_model"],
        review_llm_key=profile["review_llm_api_key"],
        review_llm_base_url=profile["review_llm_base_url"],
    )
    _reload_env_and_config()
    return {
        "message": f"配置档案 '{name}' 已保存并立即生效",
        "active_profile": effective_name,
        "profile": _public_profile(profile),
        "fallback_from": fallback_from,
        "profiles": list(data["profiles"].keys()),
    }

@router.delete("/api/settings/{profile_name}")
async def delete_setting(profile_name: str):
    data = _load_settings()
    if profile_name in data.get("profiles", {}):
        del data["profiles"][profile_name]
        if data.get("active_profile") == profile_name:
            data["active_profile"] = ""
        _save_settings(data)
    return {"message": f"已删除 '{profile_name}'"}

@router.get("/api/settings/{profile_name}/export")
async def export_profile(profile_name: str):
    """Export a profile without credentials."""
    data = _load_settings()
    profile = data.get("profiles", {}).get(profile_name)
    if not profile:
        raise HTTPException(404, "档案不存在")
    from fastapi.responses import Response
    return Response(
        content=json.dumps(_public_profile(profile), ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=api-settings.json"}
    )

@router.post("/api/settings/import")
async def import_profile(data: dict):
    """Import and validate an external settings profile."""
    try:
        req = SettingsRequest.model_validate(data)
    except Exception as exc:
        raise HTTPException(400, f"配置文件格式无效: {str(exc)[:160]}") from exc
    name = (req.profile_name or "导入的配置").strip()
    if not name or len(name) > 80:
        raise HTTPException(400, "配置名称无效")
    profiles = _load_settings()
    profiles.setdefault("profiles", {})
    existing = _normalize_audio_profile(profiles["profiles"].get(name, {}))
    profile = req.model_dump(exclude={"clear_secret_fields"})
    if not profile.get("happyhorse_api_key") and req.minimax_api_key:
        profile["happyhorse_api_key"] = req.minimax_api_key
    profile["profile_name"] = name
    profile = _merge_profile_secrets(
        profile,
        existing,
        req.clear_secret_fields,
    )
    profile = _normalize_audio_profile(profile)
    profiles["profiles"][name] = profile
    _save_settings(profiles)
    return {"message": f"已导入 '{name}'", "profiles": list(profiles["profiles"].keys())}

# ── 测试连接端点 ──────────────────────────────────────────────
class TestConnectionRequest(BaseModel):
    provider: str = "anthropic"
    key: str = ""
    secret: str = ""
    base_url: str = ""
    model: str = ""
    profile_name: str = ""


def _profile_test_value(req: TestConnectionRequest, *fields: str, provider_field: str = "") -> str:
    data = _load_settings()
    profiles = data.get("profiles", {})
    candidates = []
    if req.profile_name and profiles.get(req.profile_name):
        candidates.append(profiles[req.profile_name])
    active_name = str(data.get("active_profile") or "")
    if active_name and profiles.get(active_name):
        candidates.append(profiles[active_name])
    candidates.extend(profiles.values())
    for profile in candidates:
        if provider_field and str(profile.get(provider_field) or "").strip().lower() != req.provider.lower():
            continue
        for field in fields:
            if profile.get(field):
                return profile[field]
    return ""


def _env_test_value(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def _test_value(req: TestConnectionRequest, fields: tuple[str, ...], env_names: tuple[str, ...]) -> str:
    return req.key or _profile_test_value(req, *fields) or _env_test_value(*env_names)


def _llm_test_key(req: TestConnectionRequest) -> str:
    if req.key:
        return req.key
    if req.provider == "anthropic":
        return (
            _profile_test_value(req, "llm_key", provider_field="llm_provider")
            or _env_test_value("ANTHROPIC_API_KEY")
        )
    profile_key = _profile_test_value(req, "llm_key", provider_field="llm_provider")
    if profile_key:
        return profile_key
    # A request for one provider must not borrow a key belonging to another
    # provider.  The shared OPENAI_API_KEY is only valid for the provider that
    # is currently selected in the environment; provider-specific env names
    # remain available for explicit per-provider deployments.
    provider = str(req.provider or "").strip().lower()
    specific = _env_test_value(f"{provider.upper()}_API_KEY")
    if specific:
        return specific
    active_provider = str(os.getenv("LLM_PROVIDER") or "").strip().lower()
    # DeepSeek is the provider that historically reused the OpenAI-compatible
    # key in this application.  Do not let that shared key leak into a
    # DeepSeek probe when the process is explicitly configured for another
    # provider; preserve the legacy generic fallback for the other compatible
    # endpoints.
    if provider == "deepseek" and active_provider and active_provider != provider:
        return ""
    if active_provider == provider or provider != "deepseek":
        return _env_test_value("OPENAI_API_KEY")
    return ""


def _api_endpoint(base_url: str, default_base: str, path: str) -> str:
    base = (base_url or default_base).strip().rstrip("/")
    if path.startswith("/v1/") and base.endswith("/v1"):
        path = path[3:]
    return f"{base}{path}"


def _http_error_detail(response) -> str:
    try:
        payload = response.json()
        error = payload.get("error", payload) if isinstance(payload, dict) else payload
        if isinstance(error, dict):
            return str(error.get("message") or error.get("detail") or error)[:200]
        return str(error)[:200]
    except Exception:
        return str(response.text)[:200] or f"HTTP {response.status_code}"


def _connection_failure(exc: Exception, endpoint: str) -> dict:
    """Return an actionable transport failure without exposing credentials."""
    import httpx

    proxy = os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")
    if isinstance(exc, httpx.ProxyError):
        return {"success": False, "status": 0, "category": "proxy", "detail": "代理服务器不可用或拒绝连接"}
    if isinstance(exc, (httpx.ConnectTimeout, httpx.ReadTimeout)):
        prefix = "通过当前代理无法连接" if proxy else "无法连接"
        return {"success": False, "status": 0, "category": "network", "detail": f"{prefix} {endpoint}：连接超时"}
    if isinstance(exc, httpx.ConnectError):
        suffix = "。请检查代理地址或网络出口" if proxy else "。请检查网络出口，或在网络设置中填写可用代理"
        return {"success": False, "status": 0, "category": "network", "detail": f"无法连接 {endpoint}{suffix}"}
    return {"success": False, "status": 0, "category": "network", "detail": str(exc)[:200]}


def _probe_result(response, endpoint: str, accept_not_found: bool = False) -> dict:
    """Classify a no-cost authenticated probe consistently across providers."""
    status = response.status_code
    if 200 <= status < 300:
        return {"success": True, "status": status, "category": "ok", "detail": "连接、鉴权和接口路径正常"}
    detail = _http_error_detail(response)
    if accept_not_found and status in {400, 404} and any(
        marker in detail.lower() for marker in ("not found", "不存在", "invalid task")
    ):
        return {"success": True, "status": status, "category": "ok", "detail": "连接和鉴权正常（探测任务不存在）"}
    if status in {401, 403}:
        return {"success": False, "status": status, "category": "credentials", "detail": f"{endpoint} 拒绝认证：请检查 API Key、权限或账号开通状态"}
    if status == 404:
        return {"success": False, "status": status, "category": "endpoint", "detail": f"{endpoint} 接口路径不存在，请检查服务商地址或网关类型"}
    return {"success": False, "status": status, "category": "provider", "detail": detail}

@router.post("/api/test/llm")
async def test_llm(req: TestConnectionRequest):
    """测试 LLM 连接"""
    import httpx
    key = _llm_test_key(req)
    if not key:
        provider_label = req.provider or "LLM"
        return {"success": False, "status": 0, "detail": f"请先配置 {provider_label} API Key"}
    if req.provider == "anthropic":
        url = _api_endpoint(req.base_url, "https://api.anthropic.com", "/v1/messages")
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
        body = {"model": req.model or "claude-haiku-4-5-20251001", "max_tokens": 8, "messages": [{"role":"user","content":"hi"}]}
    else:
        default_base = LLM_DEFAULT_BASE_URLS.get(req.provider, LLM_DEFAULT_BASE_URLS["openai"])
        url = _api_endpoint(req.base_url, default_base, "/v1/chat/completions")
        headers = {"Authorization": f"Bearer {key}"}
        default_model = LLM_DEFAULT_MODELS.get(req.provider, LLM_DEFAULT_MODELS["openai"])
        body = {"model": _normalize_llm_model(req.provider, req.model or default_model), "max_tokens": 8, "messages": [{"role":"user","content":"hi"}]}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(url, json=body, headers=headers)
            return _probe_result(r, url)
    except Exception as exc:
        return _connection_failure(exc, url)

@router.post("/api/test/tts")
async def test_tts(req: TestConnectionRequest):
    """Validate DashScope MiniMax without creating a paid synthesis request."""
    if req.provider == "edge":
        try:
            import edge_tts
            return {"success": True, "status": 200, "detail": "Edge-TTS 已安装"}
        except ImportError:
            return {"success": False, "status": 500, "detail": "edge-tts 未安装"}

    import httpx
    key = _test_value(req, ("happyhorse_api_key", "minimax_api_key"), ("HAPPYHORSE_API_KEY",))
    base_url = (req.base_url or _profile_test_value(req, "happyhorse_base_url") or _env_test_value("HAPPYHORSE_BASE_URL") or "https://dashscope.aliyuncs.com").rstrip("/")
    model = req.model or "qwen3-tts-flash"
    if not key:
        return {"success": False, "status": 0, "detail": "请先配置阿里云百炼 API Key"}
    url = f"{base_url}/api/v1/services/aigc/multimodal-generation/generation"
    is_qwen3 = model.lower().startswith("qwen3-tts")
    body = {"model": model, "input": {"action": "get_voice", "voice_type": "system"}}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(url, json=body, headers={
                "Authorization": f"Bearer {key}", "Content-Type": "application/json; charset=utf-8",
            })
        if response.status_code != 200:
            detail = _http_error_detail(response)
            # Qwen3 validates the model and credentials before it rejects this
            # deliberately non-synthesis probe for the missing voice field.
            if is_qwen3 and "voice property is required" in detail.lower():
                return {"success": True, "status": 200, "detail": "百炼 Qwen3-TTS 连接正常，可直接生成配音"}
            classified = _probe_result(response, url)
            classified["detail"] = describe_dashscope_tts_error(detail)
            return classified
        payload = response.json()
        output = payload.get("output") or {}
        base_resp = output.get("base_resp") or {}
        status_code = int(base_resp.get("status_code", 0) or 0)
        if status_code != 0:
            return {
                "success": False,
                "status": status_code,
                "detail": describe_dashscope_tts_error(str(base_resp.get("status_msg") or "百炼拒绝该配置")),
            }
        count = len(output.get("system_voice") or [])
        provider_name = "Qwen3-TTS" if is_qwen3 else "MiniMax"
        return {"success": True, "status": 200, "detail": f"百炼 {provider_name} 连接正常，可用系统音色 {count} 个"}
    except Exception as exc:
        return _connection_failure(exc, url)

@router.post("/api/test/image")
async def test_image(req: TestConnectionRequest):
    """Test image authentication without starting a paid generation task."""
    if req.provider == "mock":
        return {"success": True, "status": 200, "detail": "Mock 占位图片可用"}
    if req.provider == "wan":
        import httpx
        key = _test_value(req, ("happyhorse_api_key",), ("HAPPYHORSE_API_KEY",))
        base_url = (req.base_url or _profile_test_value(req, "happyhorse_base_url") or _env_test_value("HAPPYHORSE_BASE_URL") or "").rstrip("/")
        if not key:
            return {"success": False, "status": 0, "detail": "DashScope API Key is required for Wan 2.7"}
        if "dashscope.aliyuncs.com" not in base_url.lower():
            return {"success": False, "status": 0, "detail": "Wan 2.7 requires the official DashScope endpoint; proxy and third-party gateways are unsupported"}
        # DashScope has no stable, provider-wide model-list endpoint for this
        # capability. Querying a synthetic task uses the same authenticated
        # task API as the real image client without creating a billable job.
        url = f"{base_url}/api/v1/tasks/connection-probe"
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(url, headers={"Authorization": f"Bearer {key}"})
            result = _probe_result(response, url, accept_not_found=True)
            if result["success"]:
                result["detail"] = f"DashScope connection and credentials are valid for {req.model or 'Wan 2.7'}"
            return result
        except Exception as exc:
            return _connection_failure(exc, url)
    if req.provider != "kling":
        return {"success": False, "status": 0, "detail": "当前版本仅支持 Mock 与可灵图片"}
    key = _test_value(req, ("image_gen_key", "kling_api_key"), ("IMAGE_GEN_KEY", "KLING_API_KEY"))
    if not key:
        return {"success": False, "status": 0, "detail": "请先配置 API Key"}
    import httpx
    from src.api_clients.image_kling import KLING_IMG_QUERY

    # Querying a synthetic task uses the same host and Authorization header as
    # the real image client, but does not create a billable image task.
    url = f"{KLING_IMG_QUERY}/connection-probe"
    headers = {"Authorization": f"Bearer {key}"}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(url, headers=headers)
            return _probe_result(r, url, accept_not_found=True)
    except Exception as exc:
        return _connection_failure(exc, url)

@router.post("/api/test/video")
async def test_video(req: TestConnectionRequest):
    """测试视频 API（Kling / Happy Horse）"""
    if req.provider == "mock":
        return {"success": True, "status": 200, "detail": "Mock 占位视频可用"}
    provider = "seedance" if req.provider == "seedence" else req.provider
    if provider == "seedance":
        key_fields = ("ark_api_key",)
        env_fields = ("ARK_API_KEY",)
    elif provider == "happyhorse":
        key_fields = ("happyhorse_api_key",)
        env_fields = ("HAPPYHORSE_API_KEY",)
    else:
        key_fields = ("kling_api_key", "kling_access_key")
        env_fields = ("KLING_API_KEY", "KLING_ACCESS_KEY")
    key = _test_value(
        req,
        key_fields,
        env_fields,
    )
    secret = req.secret or _profile_test_value(req, "kling_secret_key") or _env_test_value("KLING_SECRET_KEY")
    if not key:
        return {"success": False, "status": 0, "detail": "请先配置 API Key"}
    import httpx, jwt as pyjwt, time
    endpoint = "视频服务"
    try:
        if provider == "seedance":
            from src.api_clients.seedance import ARK_DEFAULT_URL, SeedanceVideoClient

            base_url = req.base_url or _profile_test_value(req, "ark_base_url") or _env_test_value("ARK_BASE_URL") or ARK_DEFAULT_URL
            client_config = SeedanceVideoClient(api_key=key, base_url=base_url, model=req.model or "doubao-seedance-2-0-fast-260128")
            url = f"{client_config.base_url}/api/v3/models"
            endpoint = url
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(url, headers=client_config._auth_headers())
            result = _probe_result(r, url)
            if not result.get("success"):
                return result
            try:
                payload = r.json()
                model_ids = {
                    str(item.get("id"))
                    for item in (payload.get("data") or [])
                    if isinstance(item, dict) and item.get("id")
                }
            except Exception:
                model_ids = set()
            requested_model = str(req.model or client_config.model)
            if model_ids and requested_model not in model_ids:
                return {
                    "success": False,
                    "status": r.status_code,
                    "category": "model",
                    "detail": f"Ark 鏈嶅姟鍙闂絾鏈彁渚� {requested_model}锛岃妫€鏌ユā鍨嬪紑閫氭潯浠�",
                }
            result["detail"] = f"Ark 连接、鉴权和模型列表正常：{requested_model}"
            return result
        if provider == "happyhorse":
            from src.api_clients.happyhorse import HappyHorseVideoClient

            base_url = req.base_url or _profile_test_value(req, "happyhorse_base_url") or _env_test_value("HAPPYHORSE_BASE_URL")
            client_config = HappyHorseVideoClient(api_key=key, base_url=base_url, model=req.model or "happyhorse-1.1-r2v")
            if client_config._format == "wavespeed":
                url = f"{client_config.base_url}/api/v3/predictions/connection-probe/result"
            else:
                _, query_prefix = client_config._dashscope_paths()
                url = f"{client_config.base_url}{query_prefix}/connection-probe"
            endpoint = url
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(
                    url,
                    headers=client_config._auth_headers(),
                )
                return _probe_result(r, url, accept_not_found=True)
        elif key.startswith("api-key-kling-"):
            token = key
            endpoint = "https://api-beijing.klingai.com/v1/videos/text2video/settings-connection-test"
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(endpoint, headers={"Authorization": f"Bearer {token}"})
                return _probe_result(r, "可灵视频服务", accept_not_found=True)
        else:
            if not secret:
                return {"success": False, "status": 0, "detail": "AK/SK 模式需要 Secret Key"}
            token = pyjwt.encode(
                {"iss": key, "exp": int(time.time()) + 1800, "nbf": int(time.time()) - 5},
                secret,
                algorithm="HS256"
            )
            endpoint = "https://api-beijing.klingai.com/v1/videos/text2video/settings-connection-test"
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(endpoint, headers={"Authorization": f"Bearer {token}"})
                return _probe_result(r, "可灵视频服务", accept_not_found=True)
    except Exception as exc:
        return _connection_failure(exc, endpoint)


@router.post("/api/test/bgm")
async def test_bgm(req: TestConnectionRequest):
    if req.provider == "mock":
        return {"success": True, "status": 200, "detail": "静音占位音轨可用"}
    return {"success": False, "status": 0, "detail": "当前版本暂未接入在线 BGM 服务"}

def _write_env(llm_provider="", llm_model="", llm_key="", llm_base="",
               default_video_api="happyhorse", kling_api_key="", kling_ak="", kling_sk="",
               kling_model="kling-v2-6", kling_mode="std",
               happyhorse_key="", happyhorse_url="", happyhorse_model="", happyhorse_res="",
               ark_key="", ark_url="", ark_model="", ark_resolution="1080p",
               img_api="mock", img_key="", kling_img_model="kling-v1",
               http_proxy="", tts_provider="qwen3", qwen_model="qwen3-tts-flash", qwen_timeout="45",
               qwen_male_voice="Ethan", qwen_female_voice="Cherry",
               minimax_model="MiniMax/speech-2.8-turbo", minimax_timeout="45", edge_tts_fallback=True,
               minimax_male_voice="", minimax_female_voice="", llm_timeout="1800", llm_stream="true",
               kimi_prompt_review_enabled=True, kimi_prompt_review_key="",
               kimi_prompt_review_model="kimi-k2.6",
               kimi_prompt_review_base_url="https://api.moonshot.cn/v1",
               review_llm_enabled=True, review_llm_provider="", review_llm_model="qwen-vl-max",
               review_llm_key="", review_llm_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"):
    """写入 .env 文件。"""
    env_path = BASE_DIR / ".env"
    llm_model = _normalize_llm_model(llm_provider, llm_model)
    lines = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").split("\n")
    to_set = {
        "HTTP_PROXY": http_proxy,
        # All provider endpoints are HTTPS. Persist both variables so a
        # restart applies the configured proxy to httpx HTTPS requests too.
        "HTTPS_PROXY": http_proxy,
        "LLM_PROVIDER": llm_provider,
        "LLM_MODEL": llm_model,
        "ANTHROPIC_API_KEY": llm_key if llm_provider == "anthropic" else "",
        "OPENAI_API_KEY": llm_key if llm_provider in OPENAI_COMPATIBLE_LLM_PROVIDERS else "",
        "OPENAI_BASE_URL": llm_base or LLM_DEFAULT_BASE_URLS.get(llm_provider, ""),
        "LLM_TIMEOUT": llm_timeout or "1800",
        "LLM_STREAM": llm_stream or "true",
        "KIMI_PROMPT_REVIEW_ENABLED": "true" if kimi_prompt_review_enabled else "false",
        "KIMI_PROMPT_REVIEW_API_KEY": kimi_prompt_review_key,
        "KIMI_PROMPT_REVIEW_MODEL": kimi_prompt_review_model or "kimi-k2.6",
        "KIMI_PROMPT_REVIEW_BASE_URL": kimi_prompt_review_base_url or "https://api.moonshot.cn/v1",
        "REVIEW_LLM_ENABLED": "true" if review_llm_enabled else "false",
        "REVIEW_LLM_PROVIDER": review_llm_provider,
        "REVIEW_LLM_MODEL": review_llm_model or "qwen-vl-max",
        "REVIEW_LLM_API_KEY": review_llm_key,
        "REVIEW_LLM_BASE_URL": review_llm_base_url or "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "DEFAULT_VIDEO_API": default_video_api,
        "KLING_API_KEY": kling_api_key,
        "KLING_ACCESS_KEY": kling_ak,
        "KLING_SECRET_KEY": kling_sk,
        "KLING_MODEL": kling_model,
        "KLING_MODE": kling_mode,
        "HAPPYHORSE_API_KEY": happyhorse_key,
        "HAPPYHORSE_BASE_URL": happyhorse_url,
        "HAPPYHORSE_MODEL": happyhorse_model,
        "HAPPYHORSE_RESOLUTION": happyhorse_res,
        "ARK_API_KEY": ark_key,
        "ARK_BASE_URL": ark_url,
        "ARK_MODEL": ark_model,
        "ARK_RESOLUTION": ark_resolution,
        "IMAGE_GEN_API": img_api,
        "IMAGE_GEN_KEY": img_key,
        "KLING_IMAGE_MODEL": kling_img_model,
        "DASHSCOPE_TTS_PROVIDER": tts_provider,
        "QWEN_TTS_MODEL": qwen_model,
        "QWEN_TTS_TIMEOUT": qwen_timeout,
        "QWEN_DEFAULT_MALE_VOICE": qwen_male_voice,
        "QWEN_DEFAULT_FEMALE_VOICE": qwen_female_voice,
        "MINIMAX_TTS_MODEL": minimax_model,
        "MINIMAX_TTS_TIMEOUT": minimax_timeout,
        "EDGE_TTS_FALLBACK": "true" if edge_tts_fallback else "false",
        "MINIMAX_DEFAULT_MALE_VOICE": minimax_male_voice,
        "MINIMAX_DEFAULT_FEMALE_VOICE": minimax_female_voice,
    }
    retired_keys = {"TTS_PROVIDER", "MINIMAX_API_KEY"}
    new_lines, seen = [], set()
    for line in lines:
        key = line.split("=")[0].strip() if "=" in line and not line.startswith("#") else ""
        if key in retired_keys:
            continue
        if key in to_set:
            new_lines.append(f"{key}={to_set[key]}")
            seen.add(key)
        else:
            new_lines.append(line)
    for key, val in to_set.items():
        if key not in seen and val:
            new_lines.append(f"{key}={val}")
    env_path.write_text("\n".join(new_lines), encoding="utf-8")


def _reload_llm_agents() -> None:
    """Replace agents that captured the previous LLM client instance."""
    script_agent.set(ScriptAgent())
    review_agent.set(ReviewAgent())


def _reload_env_and_config():
    """热加载 .env 并重置全局 LLM 客户端单例。

    用户通过前端 API 配置页面保存后，无需重启服务器即可生效。
    """
    import importlib
    from dotenv import load_dotenv
    import src.config as cfg

    # 1. 重新加载 .env → os.environ
    load_dotenv(override=True)

    # 2. 刷新 config 模块的模块级变量
    cfg.LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic")
    cfg.LLM_MODEL = _normalize_llm_model(cfg.LLM_PROVIDER, os.getenv("LLM_MODEL", "claude-sonnet-4-6"))
    cfg.ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
    cfg.OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    cfg.OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "")
    cfg.LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "1800"))
    cfg.LLM_STREAM = os.getenv("LLM_STREAM", "true").strip().lower() not in {"0", "false", "no", "off"}
    cfg.LLM_REASONING_EFFORT = os.getenv("LLM_REASONING_EFFORT", "high").strip().lower()
    try:
        cfg.LLM_MAX_TOKENS = max(1, int(os.getenv("LLM_MAX_TOKENS", "32768")))
    except (TypeError, ValueError):
        cfg.LLM_MAX_TOKENS = 32768
    try:
        cfg.LLM_MAX_INPUT_CHARS = max(1, int(os.getenv("LLM_MAX_INPUT_CHARS", "80000")))
    except (TypeError, ValueError):
        cfg.LLM_MAX_INPUT_CHARS = 80000
    # ``llm.py`` imports these settings as module globals, so hot reload must
    # update both config and the client module before replacing agents.
    import src.utils.llm as llm_module
    llm_module.LLM_REASONING_EFFORT = cfg.LLM_REASONING_EFFORT
    llm_module.LLM_MAX_TOKENS = cfg.LLM_MAX_TOKENS
    cfg.REVIEW_LLM_ENABLED = os.getenv("REVIEW_LLM_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}
    cfg.REVIEW_LLM_PROVIDER = os.getenv("REVIEW_LLM_PROVIDER", "").strip().lower()
    cfg.REVIEW_LLM_MODEL = os.getenv("REVIEW_LLM_MODEL", "qwen-vl-max")
    happyhorse_base_url = os.getenv("HAPPYHORSE_BASE_URL", "https://dashscope.aliyuncs.com").rstrip("/").lower()
    cfg.REVIEW_LLM_API_KEY = (
        os.getenv("REVIEW_LLM_API_KEY", "")
        or (os.getenv("DASHSCOPE_API_KEY", "") if not cfg.REVIEW_LLM_PROVIDER else "")
        or (os.getenv("HAPPYHORSE_API_KEY", "") if not cfg.REVIEW_LLM_PROVIDER and happyhorse_base_url in {"https://dashscope.aliyuncs.com", "http://dashscope.aliyuncs.com"} else "")
    )
    cfg.REVIEW_LLM_BASE_URL = os.getenv("REVIEW_LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    cfg.REVIEW_LLM_TIMEOUT = int(os.getenv("REVIEW_LLM_TIMEOUT", "120"))
    # 代理
    proxy = os.getenv("HTTP_PROXY", "")
    if proxy:
        os.environ['HTTPS_PROXY'] = proxy
        os.environ['HTTP_PROXY'] = proxy
    else:
        os.environ.pop('HTTPS_PROXY', None)
        os.environ.pop('HTTP_PROXY', None)
    cfg.DEFAULT_API = os.getenv("DEFAULT_VIDEO_API", "happyhorse")
    cfg.KLING_API_KEY = os.getenv("KLING_API_KEY", "")
    cfg.KLING_ACCESS_KEY = os.getenv("KLING_ACCESS_KEY", "")
    cfg.KLING_SECRET_KEY = os.getenv("KLING_SECRET_KEY", "")
    cfg.HAPPYHORSE_API_KEY = os.getenv("HAPPYHORSE_API_KEY", "")
    cfg.HAPPYHORSE_BASE_URL = os.getenv("HAPPYHORSE_BASE_URL", "https://dashscope.aliyuncs.com")
    cfg.HAPPYHORSE_MODEL = os.getenv("HAPPYHORSE_MODEL", "happyhorse-1.1-r2v")
    cfg.HAPPYHORSE_RESOLUTION = os.getenv("HAPPYHORSE_RESOLUTION", "1080p")
    cfg.ARK_API_KEY = os.getenv("ARK_API_KEY", "")
    cfg.ARK_BASE_URL = os.getenv("ARK_BASE_URL", "https://ark.cn-beijing.volces.com")
    cfg.ARK_MODEL = os.getenv("ARK_MODEL", "doubao-seedance-2-0-fast-260128")
    cfg.ARK_RESOLUTION = os.getenv("ARK_RESOLUTION", "1080p")
    cfg.IMAGE_GEN_API = os.getenv("IMAGE_GEN_API", "mock")
    cfg.IMAGE_GEN_KEY = os.getenv("IMAGE_GEN_KEY", "")
    cfg.KLING_IMAGE_MODEL = os.getenv("KLING_IMAGE_MODEL", "kling-v1")
    cfg.KLING_MODEL = os.getenv("KLING_MODEL", "kling-v2-6")
    cfg.KLING_MODE = os.getenv("KLING_MODE", "std")

    # 3. 重置 LLM 客户端单例
    import src.utils.llm as llm_mod
    llm_mod.LLM_PROVIDER = cfg.LLM_PROVIDER
    llm_mod.LLM_MODEL = cfg.LLM_MODEL
    llm_mod.ANTHROPIC_API_KEY = cfg.ANTHROPIC_API_KEY
    llm_mod.OPENAI_API_KEY = cfg.OPENAI_API_KEY
    llm_mod.OPENAI_BASE_URL = cfg.OPENAI_BASE_URL
    llm_mod.LLM_TIMEOUT = cfg.LLM_TIMEOUT
    llm_mod.LLM_STREAM = cfg.LLM_STREAM
    llm_mod._llm_client = None
    _reload_llm_agents()

    # 4. 重建图片和视频生成 Agent（热加载 Key 变更后立即生效）
    if cfg.IMAGE_GEN_API == "wan" and cfg.HAPPYHORSE_API_KEY and "dashscope.aliyuncs.com" in cfg.HAPPYHORSE_BASE_URL.lower():
        from src.api_clients.image_wan import WanImageClient
        new_image_agent = ImageGenerationAgent(api_client=WanImageClient(
            api_key=cfg.HAPPYHORSE_API_KEY, model=cfg.KLING_IMAGE_MODEL, base_url=cfg.HAPPYHORSE_BASE_URL,
        ))
    elif cfg.IMAGE_GEN_API == "kling" and (cfg.IMAGE_GEN_KEY or cfg.KLING_API_KEY):
        new_image_agent = ImageGenerationAgent(api_client=KlingImageClient(
            api_key=cfg.IMAGE_GEN_KEY or cfg.KLING_API_KEY, model=cfg.KLING_IMAGE_MODEL,
        ))
    else:
        new_image_agent = ImageGenerationAgent(api_client=MockImageClient())

    if cfg.DEFAULT_API == "kling":
        if cfg.KLING_API_KEY:
            new_video_agent = VideoGenerationAgent(api_client=KlingVideoClient(
                api_key=cfg.KLING_API_KEY, model=cfg.KLING_MODEL, mode=cfg.KLING_MODE,
            ))
        elif cfg.KLING_ACCESS_KEY and cfg.KLING_SECRET_KEY:
            new_video_agent = VideoGenerationAgent(api_client=KlingVideoClient(
                access_key=cfg.KLING_ACCESS_KEY, secret_key=cfg.KLING_SECRET_KEY,
                model=cfg.KLING_MODEL, mode=cfg.KLING_MODE,
            ))
        else:
            new_video_agent = VideoGenerationAgent()
    elif cfg.DEFAULT_API == "happyhorse":
        if cfg.HAPPYHORSE_API_KEY:
            new_video_agent = VideoGenerationAgent(api_client=HappyHorseVideoClient(
                api_key=cfg.HAPPYHORSE_API_KEY,
                base_url=cfg.HAPPYHORSE_BASE_URL,
                model=cfg.HAPPYHORSE_MODEL,
                resolution=cfg.HAPPYHORSE_RESOLUTION,
            ))
        else:
            new_video_agent = VideoGenerationAgent()
    elif cfg.DEFAULT_API in {"seedance", "seedence"}:
        if cfg.ARK_API_KEY:
            new_video_agent = VideoGenerationAgent(api_client=SeedanceVideoClient(
                api_key=cfg.ARK_API_KEY,
                base_url=cfg.ARK_BASE_URL,
                model=cfg.ARK_MODEL,
                resolution=cfg.ARK_RESOLUTION,
            ))
        else:
            new_video_agent = VideoGenerationAgent()
    else:
        new_video_agent = VideoGenerationAgent()

    new_visual_prompt_agent = VisualPromptAgent(api_provider=cfg.DEFAULT_API)
    image_gen_agent.set(new_image_agent)
    video_gen_agent.set(new_video_agent)
    visual_prompt_agent.set(new_visual_prompt_agent)
    orchestrator.visual_prompt_agent = new_visual_prompt_agent
    orchestrator.image_gen_agent = new_image_agent
    orchestrator.video_gen_agent = new_video_agent
    # Rebuild every graph that captured provider-dependent agents in closures.
    from src.graph.orchestrator import (
        build_drama_graph,
        create_stage1_graph,
        create_stage2_graph,
        create_stage4_graph,
    )
    orchestrator.graph = build_drama_graph(
        script_agent.target, new_visual_prompt_agent, new_image_agent, new_video_agent,
        review_agent, audio_agent, assembly_agent,
    )
    orchestrator.stage1 = create_stage1_graph(script_agent.target, new_visual_prompt_agent)
    orchestrator.stage2 = create_stage2_graph(new_image_agent)
    orchestrator.stage4 = create_stage4_graph(new_video_agent)


def _restore_env_from_settings() -> None:
    """Restore the active profile before rebuilding provider-dependent agents."""
    if not SETTINGS_FILE.exists():
        return
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        if not (data.get("profiles") or {}):
            return
        active = str(data.get("active_profile") or "")
        effective_name, profile, fallback_from = _select_effective_profile(data, active)
        if not profile:
            return
        if effective_name != active:
            data["active_profile"] = effective_name
            _save_settings(data)
        _write_env(
            llm_provider=profile.get("llm_provider", "anthropic"),
            llm_model=profile.get("llm_model", ""),
            llm_key=profile.get("llm_key", ""),
            llm_base=profile.get("llm_base_url", ""),
            default_video_api=profile.get("default_video_api", "happyhorse"),
            kling_api_key=profile.get("kling_api_key", ""),
            kling_ak=profile.get("kling_access_key", ""),
            kling_sk=profile.get("kling_secret_key", ""),
            kling_model=profile.get("kling_model", "kling-v2-6"),
            kling_mode=profile.get("kling_mode", "std"),
            happyhorse_key=profile.get("happyhorse_api_key", ""),
            happyhorse_url=profile.get("happyhorse_base_url", "https://dashscope.aliyuncs.com"),
            happyhorse_model=profile.get("happyhorse_model", "happyhorse-1.1-r2v"),
            happyhorse_res=profile.get("happyhorse_resolution", "1080p"),
            ark_key=profile.get("ark_api_key", ""),
            ark_url=profile.get("ark_base_url", "https://ark.cn-beijing.volces.com"),
            ark_model=profile.get("ark_model", "doubao-seedance-2-0-fast-260128"),
            ark_resolution=profile.get("ark_resolution", "1080p"),
            img_api=profile.get("image_gen_api", "mock"),
            img_key=profile.get("image_gen_key", ""),
            kling_img_model=profile.get("kling_image_model", "kling-v1"),
            tts_provider=profile.get("dashscope_tts_provider", "qwen3"),
            qwen_model=profile.get("qwen_tts_model", "qwen3-tts-flash"),
            qwen_timeout=profile.get("qwen_timeout", "45"),
        qwen_male_voice=profile.get("qwen_default_male_voice", "Ethan"),
        qwen_female_voice=profile.get("qwen_default_female_voice", "Cherry"),
        minimax_model=profile.get("minimax_model", "MiniMax/speech-2.8-turbo"),
        minimax_timeout=profile.get("minimax_timeout", "45"),
        edge_tts_fallback=profile.get("edge_tts_fallback", True),
        minimax_male_voice=profile.get("minimax_default_male_voice", ""),
        minimax_female_voice=profile.get("minimax_default_female_voice", ""),
        http_proxy=profile.get("http_proxy", ""),
        llm_timeout=profile.get("llm_timeout", "1800"),
        llm_stream=profile.get("llm_stream", "true"),
        kimi_prompt_review_enabled=profile.get("kimi_prompt_review_enabled", True),
        kimi_prompt_review_key=profile.get("kimi_prompt_review_api_key", ""),
        kimi_prompt_review_model=profile.get("kimi_prompt_review_model", "kimi-k2.6"),
        kimi_prompt_review_base_url=profile.get("kimi_prompt_review_base_url", "https://api.moonshot.cn/v1"),
    )
        print(f"[启动] 已从档案 '{active}' 恢复配置")
    except Exception as exc:
        print(f"[启动] 恢复配置失败: {exc}")

__all__ = [name for name in globals() if not name.startswith("__")]
