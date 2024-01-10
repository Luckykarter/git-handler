from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    REDIS_SERVER: str = 'localhost'
    GITHUB_SERVER: str = 'github.com'
    DEFAULT_GIT_BRANCH: str = 'main'
    GITHUB_TOKEN: Optional[str] = None


settings = Settings()
