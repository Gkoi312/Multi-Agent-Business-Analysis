import os
import sys
import json
import asyncio
from dotenv import load_dotenv
from app.logger import GLOBAL_LOGGER as log
from app.exception.custom_exception import ResearchAnalystException


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
            max_tokens = int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "2048"))
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


# ----------------------------------------------------------------------
# 🔹 Standalone Testing
# ----------------------------------------------------------------------
if __name__ == "__main__":
    try:
        loader = ModelLoader()

        # Test LLM
        llm = loader.load_llm()
        print(f"LLM Loaded: {llm}")
        result = llm.invoke("Hello, how are you?")
        print(f"LLM Result: {result.content[:200]}")

        log.info("ModelLoader test completed successfully")

    except ResearchAnalystException as e:
        log.error("Critical failure in ModelLoader test", error=str(e))
