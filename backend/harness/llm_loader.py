import os
import sys
import json
import asyncio
from dotenv import load_dotenv
from harness.observability.logger import GLOBAL_LOGGER as log
from harness.exceptions import ResearchAnalystException


class ApiKeyManager:
    """
    Loads and manages all environment-based API keys.
    """

    def __init__(self):
        load_dotenv()

        self.api_keys = {
            "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY"),
            "GOOGLE_API_KEY": os.getenv("GOOGLE_API_KEY"),
            "GROQ_API_KEY": os.getenv("GROQ_API_KEY"),
            "DEEPSEEK_API_KEY": os.getenv("DEEPSEEK_API_KEY"),
            "MOONSHOT_API_KEY": os.getenv("MOONSHOT_API_KEY"),
        }

        log.info("Initializing ApiKeyManager")

        # Log loaded key statuses without exposing secrets
        for key, val in self.api_keys.items():
            if val:
                log.info(f"{key} loaded successfully from environment")
            else:
                log.warning(f"{key} is missing in environment variables")

    def get(self, key: str):
        """
        Retrieve a specific API key.

        Args:
            key (str): Name of the API key.

        Returns:
            str | None: API key value if found.
        """
        return self.api_keys.get(key)


class ModelLoader:
    """
    Loads chat-based LLMs dynamically using environment settings.
    """

    def __init__(self):
        """
        Initialize the ModelLoader.
        """
        try:
            self.api_key_mgr = ApiKeyManager()
            log.info("ModelLoader initialized using environment-based configuration")
        except Exception as e:
            log.error("Error initializing ModelLoader", error=str(e))
            raise ResearchAnalystException("Failed to initialize ModelLoader", sys)

    # ----------------------------------------------------------------------
    # 🔹 LLM Loader
    # ----------------------------------------------------------------------
    def load_llm(self):
        """
        Load and return a chat-based LLM according to environment variables.

        Supported providers:
            - OpenAI
            - DeepSeek (OpenAI-compatible, uses ChatOpenAI)
            - Google (Gemini)
            - Groq

        Returns:
            ChatOpenAI | ChatGoogleGenerativeAI | ChatGroq: LLM instance
        """
        try:
            provider_key = os.getenv("LLM_PROVIDER", "openai")
            provider = provider_key
            model_name = os.getenv("LLM_MODEL_NAME", "gpt-4o-mini")
            temperature = float(os.getenv("LLM_TEMPERATURE", "0.2"))
            # Long merged reports (outline + body + many Sources lines) often exceed 2k output tokens;
            # capping too low cuts off mid-sentence—often at the last reference.
            max_tokens = int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "4096"))
            base_url = os.getenv("OPENAI_BASE_URL")

            log.info("Loading LLM", provider=provider, model=model_name)

            if provider == "google":
                from langchain_google_genai import ChatGoogleGenerativeAI
                llm = ChatGoogleGenerativeAI(
                    model=model_name,
                    google_api_key=self.api_key_mgr.get("GOOGLE_API_KEY"),
                    temperature=temperature,
                    max_output_tokens=max_tokens,
                )

            elif provider == "groq":
                from langchain_groq import ChatGroq
                llm = ChatGroq(
                    model=model_name,
                    api_key=self.api_key_mgr.get("GROQ_API_KEY"),
                    temperature=temperature,
                    max_tokens=max_tokens,
                )

            elif provider == "deepseek":
                from langchain_openai import ChatOpenAI
                deepseek_key = self.api_key_mgr.get("DEEPSEEK_API_KEY") or self.api_key_mgr.get("OPENAI_API_KEY")
                llm = ChatOpenAI(
                    model=model_name,
                    api_key=deepseek_key,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    base_url=base_url or "https://api.deepseek.com/v1",
                )

            elif provider == "kimi":
                from langchain_openai import ChatOpenAI
                moonshot_key = self.api_key_mgr.get("MOONSHOT_API_KEY") or self.api_key_mgr.get("OPENAI_API_KEY")
                llm = ChatOpenAI(
                    model=model_name,
                    api_key=moonshot_key,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    base_url=base_url or "https://api.moonshot.cn/v1",
                )

            elif provider == "openai":
                from langchain_openai import ChatOpenAI
                llm = ChatOpenAI(
                    model=model_name,
                    api_key=self.api_key_mgr.get("OPENAI_API_KEY"),
                    temperature=temperature,
                    max_tokens=max_tokens,
                    base_url=base_url,
                )

            else:
                log.error("Unsupported LLM provider encountered", provider=provider)
                raise ValueError(f"Unsupported LLM provider: {provider}")

            log.info("LLM loaded successfully", provider=provider, model=model_name)
            return llm

        except Exception as e:
            log.error("Error loading LLM", error=str(e))
            raise ResearchAnalystException("Failed to load LLM", sys)
