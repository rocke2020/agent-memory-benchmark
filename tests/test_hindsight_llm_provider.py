import os
import unittest
from pathlib import Path
from unittest.mock import patch


class HindsightLlmProviderTest(unittest.TestCase):
    def test_deepseek_uses_openai_chat_completions_adapter(self):
        from memory_bench.memory.hindsight import _hindsight_llm_config

        with patch.dict(
            os.environ,
            {
                "HINDSIGHT_API_LLM_PROVIDER": "openai",
                "HINDSIGHT_API_LLM_MODEL": "deepseek-v4-flash",
                "HINDSIGHT_EMBED_API_VERSION": "0.4.17",
                "DEEPSEEK_API_KEY": "test-deepseek-key",
                "DEEPSEEK_BASE_URL": "https://deepseek.example/v1",
            },
            clear=True,
        ):
            config = _hindsight_llm_config()

        self.assertEqual(
            config,
            {
                "llm_provider": "openai",
                "llm_model": "deepseek-v4-flash",
                "llm_api_key": "test-deepseek-key",
                "llm_base_url": "https://deepseek.example/v1",
            },
        )

    def test_all_four_protocol_adapters_are_configurable(self):
        from memory_bench.memory.hindsight import _hindsight_llm_config

        cases = (
            ("openai", "0.4.17", True),
            ("openai-responses", "0.9.0", True),
            ("anthropic", "0.4.17", True),
            ("gemini", "0.4.17", False),
        )
        for provider, daemon_version, supports_base_url in cases:
            environment = {
                "HINDSIGHT_API_LLM_PROVIDER": provider,
                "HINDSIGHT_API_LLM_MODEL": "test-model",
                "HINDSIGHT_API_LLM_API_KEY": "test-key",
                "HINDSIGHT_EMBED_API_VERSION": daemon_version,
            }
            expected = {
                "llm_provider": provider,
                "llm_model": "test-model",
                "llm_api_key": "test-key",
            }
            if supports_base_url:
                environment["HINDSIGHT_API_LLM_BASE_URL"] = (
                    "https://provider.example/v1"
                )
                expected["llm_base_url"] = "https://provider.example/v1"

            with (
                self.subTest(provider=provider),
                patch.dict(os.environ, environment, clear=True),
            ):
                self.assertEqual(_hindsight_llm_config(), expected)

    def test_original_gemini_defaults_are_preserved(self):
        from memory_bench.memory.hindsight import _hindsight_llm_config

        with patch.dict(
            os.environ,
            {
                "GEMINI_API_KEY": "test-gemini-key",
                "HINDSIGHT_EMBED_API_VERSION": "0.4.17",
            },
            clear=True,
        ):
            self.assertEqual(
                _hindsight_llm_config(),
                {
                    "llm_provider": "gemini",
                    "llm_model": "gemini-2.5-flash-lite",
                    "llm_api_key": "test-gemini-key",
                },
            )

    def test_native_provider_credentials_are_used_as_fallbacks(self):
        from memory_bench.memory.hindsight import _hindsight_llm_config

        cases = (
            ("openai", "OPENAI_API_KEY", "OPENAI_BASE_URL"),
            ("openai-responses", "OPENAI_API_KEY", "OPENAI_BASE_URL"),
            ("anthropic", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"),
            ("gemini", "GEMINI_API_KEY", None),
        )
        for provider, key_name, base_url_name in cases:
            environment = {
                "HINDSIGHT_API_LLM_PROVIDER": provider,
                "HINDSIGHT_API_LLM_MODEL": "test-model",
                "HINDSIGHT_EMBED_API_VERSION": "0.9.0",
                key_name: "native-key",
            }
            if base_url_name is not None:
                environment[base_url_name] = "https://native.example/v1"

            with (
                self.subTest(provider=provider),
                patch.dict(os.environ, environment, clear=True),
            ):
                config = _hindsight_llm_config()

            self.assertEqual(config["llm_api_key"], "native-key")
            if base_url_name is None:
                self.assertNotIn("llm_base_url", config)
            else:
                self.assertEqual(config["llm_base_url"], "https://native.example/v1")

    def test_openai_responses_rejects_the_pinned_daemon(self):
        from memory_bench.memory.hindsight import _hindsight_llm_config

        with (
            patch.dict(
                os.environ,
                {
                    "HINDSIGHT_API_LLM_PROVIDER": "openai-responses",
                    "HINDSIGHT_API_LLM_MODEL": "test-model",
                    "HINDSIGHT_API_LLM_API_KEY": "test-key",
                    "HINDSIGHT_EMBED_API_VERSION": "0.4.17",
                },
                clear=True,
            ),
            self.assertRaisesRegex(ValueError, "0.9.0"),
        ):
            _hindsight_llm_config()

    def test_unknown_protocol_adapter_is_rejected(self):
        from memory_bench.memory.hindsight import _hindsight_llm_config

        with (
            patch.dict(
                os.environ,
                {
                    "HINDSIGHT_API_LLM_PROVIDER": "unknown",
                    "HINDSIGHT_API_LLM_MODEL": "test-model",
                    "HINDSIGHT_API_LLM_API_KEY": "test-key",
                },
                clear=True,
            ),
            self.assertRaisesRegex(ValueError, "Unknown HINDSIGHT_API_LLM_PROVIDER"),
        ):
            _hindsight_llm_config()

    def test_openai_key_does_not_pair_with_deepseek_base_url(self):
        from memory_bench.memory.hindsight import _hindsight_llm_config

        with patch.dict(
            os.environ,
            {
                "HINDSIGHT_API_LLM_PROVIDER": "openai",
                "HINDSIGHT_API_LLM_MODEL": "test-model",
                "HINDSIGHT_EMBED_API_VERSION": "0.4.17",
                "OPENAI_API_KEY": "test-openai-key",
                "DEEPSEEK_BASE_URL": "https://deepseek.example/v1",
            },
            clear=True,
        ):
            config = _hindsight_llm_config()

        self.assertEqual(config["llm_api_key"], "test-openai-key")
        self.assertNotIn("llm_base_url", config)

    def test_deepseek_key_requires_its_matching_base_url(self):
        from memory_bench.memory.hindsight import _hindsight_llm_config

        with (
            patch.dict(
                os.environ,
                {
                    "HINDSIGHT_API_LLM_PROVIDER": "openai",
                    "HINDSIGHT_API_LLM_MODEL": "test-model",
                    "HINDSIGHT_EMBED_API_VERSION": "0.4.17",
                    "DEEPSEEK_API_KEY": "test-deepseek-key",
                    "OPENAI_BASE_URL": "https://openai.example/v1",
                },
                clear=True,
            ),
            self.assertRaisesRegex(ValueError, "DEEPSEEK_BASE_URL"),
        ):
            _hindsight_llm_config()

    def test_explicit_hindsight_base_url_requires_explicit_key(self):
        from memory_bench.memory.hindsight import _hindsight_llm_config

        with (
            patch.dict(
                os.environ,
                {
                    "HINDSIGHT_API_LLM_PROVIDER": "openai",
                    "HINDSIGHT_API_LLM_MODEL": "test-model",
                    "HINDSIGHT_EMBED_API_VERSION": "0.4.17",
                    "HINDSIGHT_API_LLM_BASE_URL": "https://custom.example/v1",
                    "OPENAI_API_KEY": "test-openai-key",
                },
                clear=True,
            ),
            self.assertRaisesRegex(ValueError, "HINDSIGHT_API_LLM_API_KEY"),
        ):
            _hindsight_llm_config()

    def test_cli_does_not_require_one_vendor_key_before_selecting_providers(self):
        from memory_bench import cli

        class ReachedDatasetResolution(Exception):
            pass

        with (
            patch.dict(os.environ, {}, clear=True),
            patch("memory_bench.cli.get_dataset", side_effect=ReachedDatasetResolution),
            self.assertRaises(ReachedDatasetResolution),
        ):
            cli.run(split="test", dataset="tempo")

    def test_prepare_isolates_daemons_by_extraction_configuration(self):
        from memory_bench.memory.hindsight import HindsightMemoryProvider

        base_environment = {
            "HINDSIGHT_API_LLM_PROVIDER": "openai",
            "HINDSIGHT_API_LLM_MODEL": "deepseek-v4-flash",
            "HINDSIGHT_API_LLM_API_KEY": "first-secret-key",
            "HINDSIGHT_API_LLM_BASE_URL": "https://deepseek.example/v1",
            "HINDSIGHT_EMBED_API_VERSION": "0.4.17",
        }
        variants = (
            {"HINDSIGHT_API_LLM_PROVIDER": "anthropic"},
            {"HINDSIGHT_API_LLM_MODEL": "different-model"},
            {"HINDSIGHT_API_LLM_API_KEY": "second-secret-key"},
            {"HINDSIGHT_API_LLM_BASE_URL": "https://different.example/v1"},
            {"HINDSIGHT_EMBED_API_VERSION": "0.4.18"},
        )
        profiles = []
        with patch("hindsight.HindsightEmbedded") as embedded:
            for changes in ({}, *variants, {}):
                environment = base_environment | changes
                with patch.dict(os.environ, environment, clear=True):
                    HindsightMemoryProvider().prepare(Path("benchmark-store"))
                profiles.append(embedded.call_args.kwargs["profile"])

        self.assertEqual(profiles[0], profiles[-1])
        self.assertEqual(len(set(profiles[:-1])), len(profiles) - 1)
        self.assertTrue(all(profile.startswith("omb-bench-") for profile in profiles))
        self.assertTrue(all("secret-key" not in profile for profile in profiles))


if __name__ == "__main__":
    unittest.main()
