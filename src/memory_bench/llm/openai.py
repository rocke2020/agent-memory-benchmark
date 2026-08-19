import json
import os
import time

from .base import EVALUATION_TEMPERATURE, LLM, Schema

_MAX_RETRIES = 6
_RETRY_BASE_DELAY = 5
_DEEPSEEK_MODEL_PREFIX = "deepseek"
_JSON_VALUE_TYPES = {
    "array": list,
    "boolean": bool,
    "object": dict,
    "string": str,
}


def _is_deepseek_model(model: str) -> bool:
    return model.rsplit("/", 1)[-1].lower().startswith(_DEEPSEEK_MODEL_PREFIX)


def _validate_json_response(data: object, schema: Schema) -> dict:
    if not isinstance(data, dict):
        raise ValueError("LLM response must be a JSON object")

    missing = [name for name in schema.required if name not in data]
    if missing:
        raise ValueError(f"LLM response is missing required fields: {missing}")

    unexpected = sorted(set(data) - set(schema.properties))
    if unexpected:
        raise ValueError(f"LLM response has unexpected fields: {unexpected}")

    for name, value in data.items():
        specification = schema.properties[name]
        expected_type = specification.get("type")
        valid_type = True
        if expected_type == "integer":
            valid_type = type(value) is int
        elif expected_type == "number":
            valid_type = type(value) in (int, float)
        elif expected_type in _JSON_VALUE_TYPES:
            valid_type = isinstance(value, _JSON_VALUE_TYPES[expected_type])
        if not valid_type:
            raise ValueError(
                f"LLM response field '{name}' must have JSON type '{expected_type}'"
            )
        if "enum" in specification and value not in specification["enum"]:
            raise ValueError(
                f"LLM response field '{name}' must be one of {specification['enum']}"
            )
        if expected_type == "array" and "items" in specification:
            item_type = specification["items"].get("type")
            for item in value:
                if item_type == "integer" and type(item) is not int:
                    raise ValueError(
                        f"LLM response field '{name}' items must be integers"
                    )

    return data


class OpenAILLM(LLM):
    def __init__(self, model: str = "gpt-4o"):
        from openai import OpenAI
        self._client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        self._model = model

    @property
    def model_id(self) -> str:
        return f"openai:{self._model}"

    def generate(self, prompt: str, schema: Schema) -> dict:
        schema_json = {
            "type": "object",
            "properties": schema.properties,
            "required": schema.required,
            "additionalProperties": False,
        }
        if _is_deepseek_model(self._model):
            messages = [
                {
                    "role": "system",
                    "content": (
                        "Return only a JSON object matching this JSON Schema exactly:\n"
                        f"{json.dumps(schema_json)}"
                    ),
                },
                {"role": "user", "content": prompt},
            ]
            response_format = {"type": "json_object"}
        else:
            messages = [{"role": "user", "content": prompt}]
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": "response",
                    "schema": schema_json,
                    "strict": True,
                },
            }
        delay = _RETRY_BASE_DELAY
        last_exc = None
        for attempt in range(_MAX_RETRIES):
            try:
                response = self._client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                    temperature=EVALUATION_TEMPERATURE,
                    response_format=response_format,
                )
            except Exception as e:
                last_exc = e
                msg = str(e)
                if "429" in msg or "rate" in msg.lower():
                    if attempt < _MAX_RETRIES - 1:
                        time.sleep(delay)
                        delay *= 2
                        continue
                raise
            text = response.choices[0].message.content
            try:
                return _validate_json_response(json.loads(text), schema)
            except (TypeError, ValueError) as e:
                last_exc = e
                if not _is_deepseek_model(self._model) or attempt >= _MAX_RETRIES - 1:
                    raise
                messages = [
                    *messages,
                    {"role": "assistant", "content": text or ""},
                    {
                        "role": "user",
                        "content": (
                            f"The previous response failed JSON Schema validation: {e}. "
                            "Return a corrected JSON object containing every required field."
                        ),
                    },
                ]
        raise RuntimeError(f"OpenAI request failed after {_MAX_RETRIES} retries: {last_exc}")
