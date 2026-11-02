"""`python -m app`: the one server (the API and the built frontend) on the port in PORT (default 8000), with logs that never hold data and, behind a proxy you control (TRUST_PROXY_HEADERS=1), the forwarded
address and scheme."""
import os

import uvicorn

from .logsafe import LOG_CONFIG
from .web import trust_proxy


def main() -> None:
    port = int(os.environ.get("PORT", "8000") or 8000)
    uvicorn.run("app.main:app", host=os.environ.get("HOST", "0.0.0.0"), port=port, log_config=LOG_CONFIG, proxy_headers=trust_proxy(), forwarded_allow_ips="*" if trust_proxy() else None,
                timeout_keep_alive=65)          # longer than the usual proxy idle time, so the proxy closes first


if __name__ == "__main__":
    main()
