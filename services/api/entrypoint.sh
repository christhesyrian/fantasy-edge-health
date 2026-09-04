#!/bin/sh
# Start the API on the port the platform assigned.
#
# Railway, Render, Fly and Cloud Run all inject $PORT and route to it, while a
# local `docker run` sets nothing. An exec-form CMD cannot expand a variable and
# a shell-form one leaves /bin/sh as PID 1, which does not forward SIGTERM: the
# lifespan's shutdown - stopping pollers before closing the event bus - would
# never run, and every deploy would kill a live draft's poller mid-cycle instead
# of stopping it. `exec` hands the process straight to uvicorn so it is PID 1
# and receives the signal itself.
set -eu

# --proxy-headers makes uvicorn read x-forwarded-proto, without which the app
# believes it is serving plain HTTP behind its platform's TLS termination and
# generates http:// redirects. --forwarded-allow-ips '*' trusts the immediate
# upstream to have set those headers honestly, which holds on a platform where
# all traffic arrives through its own proxy and does NOT hold if this container
# is ever exposed to the internet directly. Narrow it to the proxy's address if
# that ever changes.
exec uvicorn fhe.api.app:app \
    --host "${FHE_API_HOST:-0.0.0.0}" \
    --port "${PORT:-8000}" \
    --proxy-headers \
    --forwarded-allow-ips '*'
