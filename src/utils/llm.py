"""LLM client abstraction — supports Anthropic and OpenAI-compatible APIs."""

import json
from typing import Type, TypeVar
from pydantic import BaseModel

from src.config import (
    LLM_PROVIDER,
    LLM_MODEL,
    ANTHROPIC_API_KEY,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
)

T = TypeVar("T", bound=BaseModel)


class LLMClient:
    """Unified LLM client with structured output support.

    Supports:
    - Anthropic Claude (via anthropic SDK)
    - OpenAI / OpenAI-compatible APIs (via openai SDK)
    """

    def __init__(self):
        self.provider = LLM_PROVIDER
        self.model = LLM_MODEL

        if self.provider == "anthropic":
            import anthropic
            self._anthropic = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        elif self.provider == "openai":
            from openai import AsyncOpenAI
            kwargs = {"api_key": OPENAI_API_KEY}
            if OPENAI_BASE_URL:
                kwargs["base_url"] = OPENAI_BASE_URL
            self._openai = AsyncOpenAI(**kwargs)
        else:
            raise ValueError(f"Unknown LLM provider: {self.provider}")

    async def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        output_schema: Type[T],
        temperature: float = 0.7,
    ) -> T:
        """Generate a structured output (Pydantic model) from the LLM.

        Uses tool-calling / function-calling to enforce the schema.

        Args:
            system_prompt: System-level instructions
            user_prompt: User input
            output_schema: Pydantic model class for the expected output
            temperature: LLM temperature

        Returns:
            An instance of output_schema populated by the LLM.
        """
        schema_name = output_schema.__name__
        schema_json = output_schema.model_json_schema()

        if self.provider == "anthropic":
            return await self._anthropic_structured(
                system_prompt, user_prompt, output_schema, schema_name, schema_json, temperature
            )
        else:
            return await self._openai_structured(
                system_prompt, user_prompt, output_schema, schema_name, schema_json, temperature
            )

    async def generate_text(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
    ) -> str:
        """Generate free-form text from the LLM."""
        if self.provider == "anthropic":
            response = await self._anthropic.messages.create(
                model=self.model,
                max_tokens=4096,
                temperature=temperature,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            return response.content[0].text
        else:
            response = await self._openai.chat.completions.create(
                model=self.model,
                temperature=temperature,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            return response.choices[0].message.content or ""

    async def _anthropic_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        output_schema: Type[T],
        schema_name: str,
        schema_json: dict,
        temperature: float,
    ) -> T:
        """Use Anthropic tool-use to get structured output."""
        response = await self._anthropic.messages.create(
            model=self.model,
            max_tokens=4096,
            temperature=temperature,
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
    ) -> T:
        """Use OpenAI function-calling to get structured output.

        Includes retry logic for malformed JSON (common with DeepSeek).
        """
        max_retries = 2
        last_error = None

        for attempt in range(max_retries + 1):
            response = await self._openai.chat.completions.create(
                model=self.model,
                temperature=max(temperature - attempt * 0.15, 0.1),
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                tools=[{
                    "type": "function",
                    "function": {
                        "name": f"output_{schema_name}",
                        "description": f"Output the result as {schema_name}",
                        "parameters": schema_json,
                    },
                }],
                tool_choice={"type": "function", "function": {"name": f"output_{schema_name}"}},
            )

            msg = response.choices[0].message
            if not msg.tool_calls:
                last_error = "LLM returned no tool_calls"
                continue

            raw = msg.tool_calls[0].function.arguments

            try:
                return output_schema.model_validate(json.loads(raw))
            except json.JSONDecodeError as e:
                last_error = str(e)
                # Multi-strategy JSON repair
                fixed = None
                err_msg = str(e)

                if "Extra data" in err_msg:
                    # Multiple JSON objects — take first complete one
                    fixed = self._extract_first_json(raw)
                elif "Unterminated string" in err_msg:
                    fixed = self._fix_truncated_json(raw)
                else:
                    fixed = self._fix_truncated_json(raw)

                if fixed and fixed != raw:
                    try:
                        return output_schema.model_validate(json.loads(fixed))
                    except Exception:
                        pass
                print(f"  ⚠️ JSON error (attempt {attempt+1}/{max_retries+1}): {e}")

        # Final fallback: ask for plain JSON without function calling
        try:
            return await self._openai_structured_fallback(
                system_prompt, user_prompt, output_schema, temperature
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
    ) -> T:
        """Last-resort: request plain JSON output without function-calling."""
        import re
        schema_name = output_schema.__name__
        schema_json = output_schema.model_json_schema()

        resp = await self._openai.chat.completions.create(
            model=self.model,
            temperature=0.1,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
                {"role": "user", "content": f"IMPORTANT: Output ONLY valid JSON matching this schema, no markdown, no extra text:\n{json.dumps(schema_json, ensure_ascii=False)}"},
            ],
        )
        text = resp.choices[0].message.content or ""

        # Try to extract JSON from the response
        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            try:
                return output_schema.model_validate(json.loads(match.group()))
            except Exception:
                pass

        # Try the whole text
        try:
            return output_schema.model_validate(json.loads(text))
        except Exception:
            pass

        # Fix and retry
        fixed = self._fix_truncated_json(match.group() if match else text)
        if fixed:
            return output_schema.model_validate(json.loads(fixed))
        raise ValueError("Fallback extraction failed")

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
