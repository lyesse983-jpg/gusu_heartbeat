import os
import uvicorn
from dotenv import load_dotenv
from .app import create_app
from .config import Config


def main():
    load_dotenv()
    config = Config.from_env()
    host = os.getenv('COMPANION_HOST', '127.0.0.1')
    if host not in ('127.0.0.1', 'localhost', '::1') and not config.access_token:
        raise ValueError('对外监听需要设置 COMPANION_ACCESS_TOKEN。')
    if host not in ('127.0.0.1', 'localhost', '::1') and not config.access_token:
        raise SystemExit('远程监听需要先设置 COMPANION_ACCESS_TOKEN。')
    uvicorn.run(create_app(config), host=host, port=int(os.getenv('COMPANION_PORT', '8765')), workers=1, access_log=False)


if __name__ == '__main__':
    main()
