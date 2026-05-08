from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "Metrology Data Collection Platform"
    database_url: str = "postgresql+psycopg://mdcp:mdcp@localhost:5432/mdcp"
    redis_url: str = "redis://localhost:6379/0"
    read_timeout_seconds: int = 20
    read_retry_count: int = 3

    class Config:
        env_prefix = "MDCP_"
        env_file = ".env"


settings = Settings()
