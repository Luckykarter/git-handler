from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    DEFAULT_GIT_BRANCH: str = 'main'


settings = Settings()
