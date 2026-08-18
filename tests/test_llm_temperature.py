import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from memory_bench.llm.base import Schema
from memory_bench.llm.gemini import GeminiLLM
from memory_bench.llm.groq import GroqLLM
from memory_bench.llm.openai import OpenAILLM


_SCHEMA = Schema(
    properties={"answer": {"type": "string"}},
    required=["answer"],
)


class RecordingCompletions:
    def __init__(self, content='{"answer": "ok"}'):
        self.request = None
        self.content = content

    def create(self, **kwargs):
        self.request = kwargs
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))]
        )


class RecordingGeminiModels:
    def __init__(self):
        self.request = None

    def generate_content(self, **kwargs):
        self.request = kwargs
        return SimpleNamespace(parsed={"answer": "ok"})


class LlmTemperatureTest(unittest.TestCase):
    def test_deepseek_uses_json_object_mode_with_schema_instruction(self):
        completions = RecordingCompletions()
        llm = OpenAILLM.__new__(OpenAILLM)
        llm._client = SimpleNamespace(
            chat=SimpleNamespace(completions=completions)
        )
        llm._model = "deepseek-v4-flash"

        llm.generate("test prompt", _SCHEMA)

        self.assertEqual(
            completions.request["response_format"],
            {"type": "json_object"},
        )
        self.assertEqual(completions.request["messages"][0]["role"], "system")
        self.assertIn("JSON Schema", completions.request["messages"][0]["content"])

    def test_deepseek_rejects_json_that_does_not_match_schema(self):
        completions = RecordingCompletions('{"answer": false}')
        llm = OpenAILLM.__new__(OpenAILLM)
        llm._client = SimpleNamespace(
            chat=SimpleNamespace(completions=completions)
        )
        llm._model = "deepseek-v4-flash"

        with self.assertRaisesRegex(ValueError, "answer"):
            llm.generate("test prompt", _SCHEMA)

    def test_openai_client_constructs_with_lowercase_socks_proxy(self):
        with patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "test-key",
                "OPENAI_BASE_URL": "https://deepseek.example/v1",
                "all_proxy": "socks5://127.0.0.1:1080",
            },
            clear=True,
        ):
            llm = OpenAILLM("deepseek-v4-flash")

        llm._client.close()

    def test_chat_completion_providers_use_zero_temperature(self):
        for llm_class in (OpenAILLM, GroqLLM):
            completions = RecordingCompletions()
            llm = llm_class.__new__(llm_class)
            llm._client = SimpleNamespace(
                chat=SimpleNamespace(completions=completions)
            )
            llm._model = "test-model"

            with self.subTest(provider=llm_class.__name__):
                llm.generate("test prompt", _SCHEMA)

                self.assertEqual(completions.request["temperature"], 0.0)

    def test_gemini_uses_zero_temperature(self):
        models = RecordingGeminiModels()
        llm = GeminiLLM.__new__(GeminiLLM)
        llm._client = SimpleNamespace(models=models)
        llm._model = "test-model"

        llm.generate("test prompt", _SCHEMA)

        self.assertEqual(models.request["config"].temperature, 0.0)


if __name__ == "__main__":
    unittest.main()
