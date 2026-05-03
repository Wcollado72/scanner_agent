from dataclasses import dataclass
from pathlib import Path
import os

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True, slots=True)
class Settings:
    gemini_api_key: str | None
    google_client_secrets_path: Path
    google_token_path: Path
    default_scan_path: Path
    log_level: str

    @staticmethod
    def load() -> "Settings":
        home = Path.home()
        default_documents = home / os.getenv("DEFAULT_SCAN_PATH", "Documents")

        return Settings(
            gemini_api_key=os.getenv("GEMINI_API_KEY"),
            google_client_secrets_path=Path(
                os.getenv("GOOGLE_CLIENT_SECRETS_PATH", "credentials.json")
            ),
            google_token_path=Path(os.getenv("GOOGLE_TOKEN_PATH", "token.json")),
            default_scan_path=default_documents,
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )