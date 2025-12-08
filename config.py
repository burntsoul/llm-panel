# config.py
import os


class Settings:
    """
    Yksi paikka kaikille asetuksille.
    Arvot tulevat ensisijaisesti ympäristömuuttujista,
    muuten käytetään näitä oletuksia.
    """

    def __init__(self) -> None:
        # IPMI / LO100
        self.LO100_IP = os.getenv("LO100_IP", "192.168.8.33")
        self.LO100_USER = os.getenv("LO100_USER", "admin")
        self.LO100_PASS = os.getenv("LO100_PASS", "Azcxn669")

        # LLM-palvelin (Ollama)
        self.LLM_HOST = os.getenv("LLM_HOST", "192.168.8.34")
        self.LLM_PORT = int(os.getenv("LLM_PORT", "11434"))

        # Glances-API llm-serverillä
        self.GLANCES_API_BASE = (
            os.getenv("GLANCES_API_BASE")
            or f"http://{self.LLM_HOST}:61208/api/3"
        )

        # Idle-logiikka
        self.CPU_BUSY_THRESHOLD_FOR_IDLE = float(
            os.getenv("CPU_BUSY_THRESHOLD_FOR_IDLE", "20")
        )  # %
        self.CPU_POLL_INTERVAL_SECONDS = float(
            os.getenv("CPU_POLL_INTERVAL_SECONDS", "10")
        )  # s

        # Boot & idle timeoutit
        self.LLM_BOOT_TIMEOUT = int(os.getenv("LLM_BOOT_TIMEOUT", "180"))
        self.LLM_POLL_INTERVAL = float(os.getenv("LLM_POLL_INTERVAL", "5"))
        self.LLM_IDLE_SECONDS = int(
            os.getenv("LLM_IDLE_SECONDS", "3600")
        )

        # Mitkä mallit näkyvät, vaikka LLM olisi down
        default_models_raw = os.getenv(
            "DEFAULT_MODELS",
            "deepseek-coder:1.3b,deepseek-coder:6.7b",
        )
        self.DEFAULT_MODELS = [
            m.strip() for m in default_models_raw.split(",") if m.strip()
        ]

        # OpenAI-yhteensopiva base-URL (sama host/port kuin Ollama)
        self.LLM_SERVER_BASE = os.getenv(
            "LLM_SERVER_BASE",
            f"http://{self.LLM_HOST}:{self.LLM_PORT}",
        )


settings = Settings()
