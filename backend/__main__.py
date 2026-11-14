"""Launch the backend with its validated binding configuration."""
import argparse

import uvicorn

from backend.config import settings
from backend.utils.config_validation import ConfigValidator


def main():
    parser = argparse.ArgumentParser(description='Run SovereignAI backend')
    parser.add_argument('--reload', action='store_true', help='Reload after code changes')
    args = parser.parse_args()
    ConfigValidator(settings).enforce_or_exit()
    uvicorn.run('backend.main:app', host=settings.backend_host, port=settings.backend_port, reload=args.reload)


if __name__ == '__main__':
    main()
