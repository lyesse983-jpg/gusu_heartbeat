"""Runtime configuration. API keys stay in server environment variables."""
from dataclasses import dataclass
from pathlib import Path
import os
from zoneinfo import ZoneInfo


@dataclass
class Config:
    data_dir: Path = Path('data')
    mode: str = 'demo'
    provider: str = 'anthropic'
    api_key: str = ''
    model: str = ''
    base_url: str | None = None
    timezone: str = 'Asia/Shanghai'
    access_token: str = ''
    max_steps: int = 8

    @classmethod
    def from_env(cls):
        kind = os.getenv('COMPANION_PROVIDER', 'anthropic')
        value = cls(
            data_dir=Path(os.getenv('COMPANION_DATA_DIR', 'data')).resolve(),
            mode=os.getenv('COMPANION_MODE', 'demo'),
            provider=kind,
            api_key=os.getenv('COMPANION_API_KEY', ''),
            model=os.getenv('COMPANION_MODEL', ''),
            base_url=os.getenv('COMPANION_BASE_URL') or None,
            timezone=os.getenv('COMPANION_TIMEZONE', 'Asia/Shanghai'),
            access_token=os.getenv('COMPANION_ACCESS_TOKEN', ''),
        )
        if value.mode not in ('demo', 'live'):
            raise ValueError('COMPANION_MODE must be demo or live')
        if kind not in ('anthropic', 'openai'):
            raise ValueError('COMPANION_PROVIDER must be anthropic or openai')
        ZoneInfo(value.timezone)
        if value.mode == 'live' and (not value.api_key or not value.model):
            raise ValueError('Live mode requires COMPANION_API_KEY and COMPANION_MODEL')
        return value
