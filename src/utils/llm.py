"""LLM client abstraction — supports Anthropic and OpenAI-compatible APIs."""

import json
import re
import sys
import asyncio
from typing import Any, Type, TypeVar
from pydantic import BaseModel

# 修复 Windows 中文环境 print emoji 崩溃
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

from src.config import (
    LLM_PROVIDER,
    LLM_MODEL,
    ANTHROPIC_API_KEY,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    LLM_TIMEOUT,
    LLM_STREAM,
)

T = TypeVar("T", bound=BaseModel)


def _strip_json_fence(text: str) -> str:
    """Remove markdown json fences so raw JSON can be validated."""
    t = (text or "").strip()
    t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    return t

OPENAI_COMPATIBLE_BASE_URLS = {
    "deepseek": "https://api.deepseek.com/v1",
    "kimi": "https://api.moonshot.cn/v1",
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
}

PLAIN_JSON_STRUCTURED_PROVIDERS = {"deepseek", "kimi", "qwen"}

# A 60-second timeout is too short for the long structured requests used by
# script analysis and visual decomposition.  Keep the setting user-visible,
# but enforce a safe floor for those stages and a smaller floor for ordinary
# prompts.  The transport client itself is configured with the ordinary floor;
# long-stage requests pass their larger timeout explicitly.
DEFAULT_LLM_TIMEOUT = 300
MIN_LLM_TIMEOUT = 120
LONG_LLM_TIMEOUT = 300
MAX_LLM_TRANSPORT_RETRIES = 1


class LLMRequestError(RuntimeError):
    """Actionable error raised after an LLM transport request is exhausted."""

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        model: str,
        phase: str,
        timeout_sec: int,
        attempts: int,
        timed_out: bool = False,
        cause: BaseException | None = None,
    ):
        super().__init__(message)
        self.provider = provider
        self.model = model
        self.phase = phase
        self.timeout_sec = timeout_sec
        self.attempts = attempts
        self.timed_out = timed_out
        self.cause = cause
        self.status_code = int(getattr(cause, "status_code", 0) or 0)


def _is_transient_llm_error(exc: BaseException) -> bool:
    """Retry only transport/rate/server failures, never prompt validation."""
    status = int(getattr(exc, "status_code", 0) or 0)
    if status == 429 or status >= 500:
        return True
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, ConnectionError)):
        return True
    name = exc.__class__.__name__.lower()
    text = str(exc).lower()
    return any(
        marker in name or marker in text
        for marker in ("timeout", "connection", "network", "temporarily unavailable", "reset by peer")
    )


class LLMClient:
    """Unified LLM client with structured output support.

    Supports:
    - Anthropic Claude (via anthropic SDK)
    - OpenAI / OpenAI-compatible APIs (via openai SDK)
    """

    def __init__(self):
        self.provider = LLM_PROVIDER
        self.model = LLM_MODEL
        self._anthropic = None
        self._openai = None

        # 如果 API Key 已配置，立即初始化客户端；否则延迟到首次调用
        if self.provider == "anthropic" and ANTHROPIC_API_KEY and ANTHROPIC_API_KEY != "your-api-key-here":
            import anthropic
            self._anthropic = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        elif self.provider != "anthropic" and OPENAI_API_KEY:
            self._init_openai()

    def _init_openai(self):
        """延迟初始化 OpenAI 兼容客户端。"""
        from openai import AsyncOpenAI
        from src.config import OPENAI_API_KEY, OPENAI_BASE_URL
        base_url = OPENAI_BASE_URL or OPENAI_COMPATIBLE_BASE_URLS.get(self.provider, "")
        kwargs = {
            "api_key": OPENAI_API_KEY,
            "timeout": self._request_timeout(""),
            "max_retries": 0,
        }
        if base_url:
            kwargs["base_url"] = base_url
        self._openai = AsyncOpenAI(**kwargs)

    @staticmethod
    def _configured_timeout() -> int:
        try:
            return max(30, int(LLM_TIMEOUT or DEFAULT_LLM_TIMEOUT))
        except (TypeError, ValueError):
            return DEFAULT_LLM_TIMEOUT

    def _request_timeout(self, phase: str = "") -> int:
        """Return the effective timeout for one request phase."""
        configured = self._configured_timeout()
        normalized_phase = str(phase or "").strip().lower()
        if normalized_phase.startswith("script.") or normalized_phase in {"script", "visualize"}:
            return max(configured, LONG_LLM_TIMEOUT)
        return max(configured, MIN_LLM_TIMEOUT)

    def _llm_request_error(
        self,
        exc: BaseException,
        *,
        phase: str,
        timeout_sec: int,
        attempts: int,
    ) -> LLMRequestError:
        lowered = str(exc).lower()
        timed_out = (
            isinstance(exc, (asyncio.TimeoutError, TimeoutError))
            or "timeout" in lowered
            or "timed out" in lowered
        )
        label = str(phase or "llm.request")
        if timed_out:
            message = (
                f"LLM request timed out after {timeout_sec}s "
                f"(phase={label}, provider={self.provider}, model={self.model}, attempts={attempts})"
            )
        else:
            message = (
                f"LLM request failed (phase={label}, provider={self.provider}, "
                f"model={self.model}, attempts={attempts}): {str(exc)[:500]}"
            )
        return LLMRequestError(
            message,
            provider=self.provider,
            model=self.model,
            phase=label,
            timeout_sec=timeout_sec,
            attempts=attempts,
            timed_out=timed_out,
            cause=exc,
        )

    async def _request_completion(
        self,
        kwargs: dict[str, Any],
        *,
        phase: str = "",
        telemetry: dict[str, Any] | None = None,
    ):
        """Call OpenAI-compatible chat once, with one bounded transport retry."""
        timeout_sec = self._request_timeout(phase)
        deadline = asyncio.get_running_loop().time() + timeout_sec
        last_error: BaseException | None = None
        for attempt in range(1, MAX_LLM_TRANSPORT_RETRIES + 2):
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                last_error = TimeoutError(f"LLM request exceeded {timeout_sec}s deadline")
                break
            request_kwargs = dict(kwargs)
            request_kwargs.setdefault("timeout", timeout_sec)
            if telemetry is not None:
                telemetry["transport_attempts"] = int(telemetry.get("transport_attempts", 0)) + 1
            try:
                # SDK-level timeouts have not been reliable across every
                # OpenAI-compatible provider. Keep one application deadline
                # across retries so a hung socket cannot pin a project task.
                return await asyncio.wait_for(
                    self._openai.chat.completions.create(**request_kwargs),
                    timeout=remaining,
                )
            except Exception as exc:
                last_error = exc
                can_retry = (
                    attempt <= MAX_LLM_TRANSPORT_RETRIES
                    and _is_transient_llm_error(exc)
                    and asyncio.get_running_loop().time() < deadline
                )
                if not can_retry:
                    break
                if telemetry is not None:
                    telemetry["transport_retries"] = int(telemetry.get("transport_retries", 0)) + 1
                await asyncio.sleep(min(2 ** (attempt - 1), 2))
        assert last_error is not None
        raise self._llm_request_error(
            last_error,
            phase=phase,
            timeout_sec=timeout_sec,
            attempts=attempt,
        ) from last_error

    def _provider_temperature(self, temperature: float) -> float:
        if self.provider == "deepseek":
            # DeepSeek is used for deterministic script/prompt structure. Keep
            # one effective value across all agents instead of silently
            # varying with each agent's legacy default.
            return 0.25
        if self.provider == "kimi":
            return 1
        return temperature

    def _ensure_client(self):
        """确保客户端已初始化，每次调用前检查以支持热加载。"""
        from src.config import ANTHROPIC_API_KEY, OPENAI_API_KEY
        if self.provider == "anthropic":
            if self._anthropic is None:
                import anthropic
                if not ANTHROPIC_API_KEY or ANTHROPIC_API_KEY == "your-api-key-here":
                    raise ValueError("请先在「API 配置」页面设置 Anthropic API Key")
                self._anthropic = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        else:
            if self._openai is None:
                if not OPENAI_API_KEY:
                    raise ValueError("请先在「API 配置」页面设置 LLM API Key（DeepSeek/OpenAI 等）")
                self._init_openai()

    async def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        output_schema: Type[T],
        temperature: float = 0.7,
        telemetry: dict[str, Any] | None = None,
        phase: str = "",
        schema_defaults: dict[str, Any] | None = None,
    ) -> T:
        """Generate a structured output (Pydantic model) from the LLM."""
        self._ensure_client()
        if telemetry is not None:
            telemetry.update({
                "attempts": 0,
                "repair_attempts": 0,
                "fallback_used": False,
                "input_chars": len(system_prompt) + len(user_prompt),
                "output_chars": 0,
                "phase": phase or "llm.structured",
                "provider": self.provider,
                "model": self.model,
                "configured_timeout_sec": self._configured_timeout(),
                "timeout_sec": self._request_timeout(phase),
                "transport_attempts": 0,
                "transport_retries": 0,
            })
        schema_name = output_schema.__name__
        schema_json = output_schema.model_json_schema()

        if self.provider == "anthropic":
            result = await self._anthropic_structured(
                system_prompt, user_prompt, output_schema, schema_name, schema_json, temperature, telemetry, phase,
            )
        else:
            result = await self._openai_structured(
                system_prompt, user_prompt, output_schema, schema_name, schema_json, temperature, telemetry, phase,
                schema_defaults,
            )
        if telemetry is not None:
            telemetry["output_chars"] = len(result.model_dump_json())
        return result

    async def generate_text(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
        phase: str = "",
        telemetry: dict[str, Any] | None = None,
    ) -> str:
        """Generate free-form text from the LLM."""
        self._ensure_client()
        if telemetry is not None:
            telemetry.update({
                "phase": phase or "llm.text",
                "provider": self.provider,
                "model": self.model,
                "input_chars": len(system_prompt) + len(user_prompt),
                "configured_timeout_sec": self._configured_timeout(),
                "timeout_sec": self._request_timeout(phase),
                "transport_attempts": 0,
                "transport_retries": 0,
            })
        if self.provider == "anthropic":
            response = await self._anthropic.messages.create(
                model=self.model,
                max_tokens=4096,
                temperature=self._provider_temperature(temperature),
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            return response.content[0].text
        else:
            kwargs = {
                "model": self.model,
                "temperature": self._provider_temperature(temperature),
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            }
            return await self._openai_chat_content(kwargs, phase=phase, telemetry=telemetry)

    async def analyze_image(
        self,
        image_path: str,
        prompt: str = "Describe this character's physical appearance in extreme detail.",
        system: str = "",
        phase: str = "asset.vision",
    ) -> str:
        """Analyze an image using vision-capable LLM and return a text description.

        Sends a base64-encoded image to the LLM with a descriptive prompt.
        Works with DeepSeek (OpenAI-compatible), GPT-4o, Claude Vision, etc.

        Args:
            image_path: Path to the local image file.
            prompt: What to ask about the image.
            system: Optional custom system prompt (uses character default if empty).

        Returns:
            Detailed text description of the image content.
        """
        import base64
        from pathlib import Path

        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"Image not found: {image_path}")

        image_data = path.read_bytes()
        ext = path.suffix.lower().lstrip('.')
        mime_map = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}
        mime_type = mime_map.get(ext, "image/png")
        data_uri = f"data:{mime_type};base64,{base64.b64encode(image_data).decode('ascii')}"

        self._ensure_client()

        default_system = """You are a forensic character portrait analyst. Your task is to describe the person in the image using a strict structured format. Every field must be filled. Use ONLY objective visual facts — no subjective judgments like "beautiful" or "handsome".

Output format (one line, comma-separated):
Gender, Age estimate, Height/build, Face shape, Skin tone, Eye shape and color, Nose shape, Lip shape, Hair style color and length, Eyebrow style, Body type, Clothing colors and materials, Accessories, Distinguishing features

Example output:
Male, 25 years old, 178cm slim build, oval face, fair skin, deep-set almond eyes dark brown, high straight nose bridge, medium fullness lips, short neat black hair with side part, natural straight eyebrows, lean athletic build, wearing a crisp white dress shirt and navy blazer, silver wristwatch, small mole under left eye"""

        sys_prompt = system if system else default_system

        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": data_uri}},
            ]},
        ]

        response = await self._request_completion(
            {
                "model": self.model,
                "max_tokens": 1024,
                "temperature": self._provider_temperature(0.3),
                "messages": messages,
            },
            phase=phase,
        )
        result = response.choices[0].message.content or ""
        print(f"  🔍 Vision analysis: {image_path} → {len(result)} chars")
        return result

    async def _anthropic_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        output_schema: Type[T],
        schema_name: str,
        schema_json: dict,
        temperature: float,
        telemetry: dict[str, Any] | None = None,
        phase: str = "",
    ) -> T:
        """Use Anthropic tool-use to get structured output."""
        if telemetry is not None:
            telemetry["attempts"] += 1
        response = await self._anthropic.messages.create(
            model=self.model,
            max_tokens=4096,
            temperature=self._provider_temperature(temperature),
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            tools=[{
                "name": f"output_{schema_name}",
                "description": f"Output the result as {schema_name}",
                "input_schema": schema_json,
            }],
            tool_choice={"type": "tool", "name": f"output_{schema_name}"},
        )

        # Extract tool call result
        for block in response.content:
            if block.type == "tool_use":
                return output_schema.model_validate(block.input)
        raise ValueError("LLM did not return a tool_use block")

    async def _openai_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        output_schema: Type[T],
        schema_name: str,
        schema_json: dict,
        temperature: float,
        telemetry: dict[str, Any] | None = None,
        phase: str = "",
        schema_defaults: dict[str, Any] | None = None,
    ) -> T:
        """Use OpenAI function-calling to get structured output.

        Includes retry logic for malformed JSON (common with DeepSeek).
        """
        if self.provider in PLAIN_JSON_STRUCTURED_PROVIDERS:
            if telemetry is not None:
                telemetry["fallback_used"] = True
                telemetry["attempts"] += 1
            return await self._openai_structured_fallback(
                system_prompt, user_prompt, output_schema, temperature, phase=phase, telemetry=telemetry, schema_defaults=schema_defaults
            )

        max_retries = 2
        last_error = None

        for attempt in range(max_retries + 1):
            if telemetry is not None:
                telemetry["attempts"] += 1
            kwargs = {
                "model": self.model,
                "temperature": self._provider_temperature(max(temperature - attempt * 0.15, 0.1)),
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "tools": [{
                    "type": "function",
                    "function": {
                        "name": f"output_{schema_name}",
                        "description": f"Output the result as {schema_name}",
                        "parameters": schema_json,
                    },
                }],
                "tool_choice": {"type": "function", "function": {"name": f"output_{schema_name}"}},
            }
            response = await self._request_completion(kwargs, phase=phase, telemetry=telemetry)

            msg = response.choices[0].message
            if not msg.tool_calls:
                last_error = "LLM returned no tool_calls"
                continue

            raw = msg.tool_calls[0].function.arguments

            try:
                return output_schema.model_validate(json.loads(raw))
            except json.JSONDecodeError as e:
                last_error = str(e)
                if telemetry is not None:
                    telemetry["repair_attempts"] += 1
                # Multi-strategy JSON repair
                fixed = None
                err_msg = str(e)

                if "Extra data" in err_msg:
                    # Multiple JSON objects — take first complete one
                    fixed = self._extract_first_json(raw)
                elif "Unterminated string" in err_msg:
                    fixed = self._fix_truncated_json(raw)
                else:
                    # Try generic repair (handles missing commas, trailing commas, etc.)
                    fixed = self._fix_truncated_json(raw)
                    if not fixed:
                        # Try inserting missing commas (common DeepSeek issue)
                        fixed = self._fix_missing_commas(raw, e)

                if fixed and fixed != raw:
                    try:
                        return output_schema.model_validate(json.loads(fixed))
                    except Exception:
                        pass
                try:
                    print(f"  [WARN] JSON error (attempt {attempt+1}/{max_retries+1}): {e}")
                except Exception:
                    pass

        # Final fallback: ask for plain JSON without function calling
        try:
            if telemetry is not None:
                telemetry["fallback_used"] = True
                telemetry["attempts"] += 1
            return await self._openai_structured_fallback(
                system_prompt, user_prompt, output_schema, temperature, phase=phase, telemetry=telemetry, schema_defaults=schema_defaults
            )
        except Exception as e:
            raise ValueError(
                f"Failed to get valid structured output after {max_retries+1} attempts + fallback: {last_error} | fallback: {e}"
            )

    async def _openai_structured_fallback(
        self,
        system_prompt: str,
        user_prompt: str,
        output_schema: Type[T],
        temperature: float,
        *,
        phase: str = "",
        telemetry: dict[str, Any] | None = None,
        schema_defaults: dict[str, Any] | None = None,
    ) -> T:
        """Last-resort: request plain JSON output without function-calling."""
        import re
        schema_name = output_schema.__name__
        schema_json = output_schema.model_json_schema()

        kwargs = {
            "model": self.model,
            "temperature": self._provider_temperature(temperature),
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
                {"role": "user", "content": f"IMPORTANT: Output ONLY valid JSON matching this schema, no markdown, no extra text:\n{json.dumps(schema_json, ensure_ascii=False)}"},
            ],
        }
        text = await self._openai_chat_content(kwargs, phase=phase, telemetry=telemetry)

        # Fences are common with DeepSeek: strip them before any parse.
        cleaned = _strip_json_fence(text)
        candidates = []
        try:
            candidates.append(json.loads(cleaned))
        except Exception:
            match = re.search(r"\{[\s\S]*\}", cleaned)
            if match:
                try:
                    candidates.append(json.loads(match.group()))
                except Exception:
                    pass

        for obj in candidates:
            if not isinstance(obj, dict):
                continue
            if schema_defaults and isinstance(schema_defaults, dict):
                for key, value in schema_defaults.items():
                    obj.setdefault(key, value)
            try:
                return output_schema.model_validate(obj)
            except Exception:
                continue

        # Fix and retry on the cleaned text
        try:
            fixed = self._fix_truncated_json(cleaned)
            if fixed:
                return output_schema.model_validate(json.loads(fixed))
        except Exception:
            pass
        raise ValueError("Fallback extraction failed")

    async def _openai_chat_content(
        self,
        kwargs: dict[str, Any],
        *,
        phase: str = "",
        telemetry: dict[str, Any] | None = None,
    ) -> str:
        """Return chat content, using streaming for Kimi/OpenAI-compatible slow calls."""
        use_stream = bool(LLM_STREAM and self.provider == "kimi")
        if not use_stream:
            resp = await self._request_completion(kwargs, phase=phase, telemetry=telemetry)
            return resp.choices[0].message.content or ""

        timeout_sec = self._request_timeout(phase)
        last_error: BaseException | None = None
        for attempt in range(1, MAX_LLM_TRANSPORT_RETRIES + 2):
            chunks: list[str] = []
            request_kwargs = dict(kwargs)
            request_kwargs.update(stream=True, timeout=timeout_sec)
            if telemetry is not None:
                telemetry["transport_attempts"] = int(telemetry.get("transport_attempts", 0)) + 1
            try:
                stream = await self._openai.chat.completions.create(**request_kwargs)
                async for event in stream:
                    if not getattr(event, "choices", None):
                        continue
                    delta = getattr(event.choices[0], "delta", None)
                    content = getattr(delta, "content", None)
                    if content:
                        chunks.append(content)
                return "".join(chunks)
            except Exception as exc:
                last_error = exc
                can_retry = attempt <= MAX_LLM_TRANSPORT_RETRIES and _is_transient_llm_error(exc)
                if not can_retry:
                    break
                if telemetry is not None:
                    telemetry["transport_retries"] = int(telemetry.get("transport_retries", 0)) + 1
                await asyncio.sleep(min(2 ** (attempt - 1), 2))
        assert last_error is not None
        raise self._llm_request_error(
            last_error,
            phase=phase,
            timeout_sec=timeout_sec,
            attempts=attempt,
        ) from last_error

    @staticmethod
    def _extract_first_json(raw: str) -> str | None:
        """Extract the first complete JSON object from text with extra content."""
        import re
        depth = 0
        start = raw.find('{')
        if start == -1:
            return None
        in_string = False
        escape = False
        for i, ch in enumerate(raw[start:], start):
            if escape:
                escape = False
                continue
            if ch == '\\':
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    return raw[start:i+1]
        return None

    @staticmethod
    def _fix_missing_commas(raw: str, json_error: json.JSONDecodeError) -> str | None:
        """Try to fix missing commas in JSON — common DeepSeek function-calling bug.

        DeepSeek sometimes produces JSON like:
            {"key": "value" "next": 123}
        instead of:
            {"key": "value", "next": 123}
        """
        if not raw or raw[0] not in '{[':
            return None

        import re
        pos = json_error.pos
        # Try inserting a comma at positions around the error
        candidates = []
        if pos > 0:
            # Insert comma just before the error position
            candidates.append(raw[:pos] + ',' + raw[pos:])
            # Try replacing the character before error with comma
            if pos > 1:
                candidates.append(raw[:pos-1] + ',' + raw[pos:])
            # Try inserting after previous quote or bracket
            for i in range(max(0, pos-20), pos):
                if raw[i] in '"}]\'"':
                    candidates.append(raw[:i+1] + ',' + raw[i+1:])
                    break
        for i in range(pos, min(len(raw), pos+20)):
            if raw[i] in '"{[':
                candidates.append(raw[:i] + ',' + raw[i:])
                break

        for candidate in candidates[:5]:  # max 5 attempts
            try:
                json.loads(candidate)
                return candidate
            except json.JSONDecodeError:
                continue
        return None

    @staticmethod
    def _fix_truncated_json(raw: str) -> str | None:
        """Try to fix malformed JSON from LLM output.

        Common DeepSeek issues:
        - Trailing comma before closing brace/bracket: {"a": 1,}
        - Missing braces from truncation
        - Unterminated strings (missing closing quote)
        - Unescaped newlines in strings
        """
        if not raw or raw[0] not in '{[':
            return None

        import re

        # 1. Remove trailing commas before } or ]
        fixed = re.sub(r',\s*(\}|\])', r'\1', raw)

        # 2. Remove unescaped control characters in strings
        fixed = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', ' ', fixed)

        # 3. Fix unterminated strings — close the last unclosed quote
        result = []
        in_string = False
        escape = False
        for ch in fixed:
            if escape:
                escape = False
                result.append(ch)
                continue
            if ch == '\\':
                escape = True
                result.append(ch)
                continue
            if ch == '"':
                in_string = not in_string
            result.append(ch)
        if in_string:
            result.append('"')
        fixed = ''.join(result)

        # 4. Complete unmatched braces/brackets
        stack = []
        in_string = False
        escape = False
        for ch in fixed:
            if escape:
                escape = False
                continue
            if ch == '\\':
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch in '{[':
                stack.append('}' if ch == '{' else ']')
            elif ch in '}]':
                if stack and stack[-1] == ch:
                    stack.pop()

        if stack:
            for ch in reversed(stack):
                fixed += ch

        return fixed if fixed != raw else None


# Singleton
_llm_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    """Get or create the singleton LLM client."""
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client
