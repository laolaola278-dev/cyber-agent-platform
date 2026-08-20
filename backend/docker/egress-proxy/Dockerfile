# Phase 28.5 -- controlled egress proxy container.
#
# The sandbox containers' ONLY route to the outside world. Denies
# private/metadata destinations at the network layer (layer-2 SSRF defense)
# and forwards public traffic. Minimal, pinned, non-root.

FROM python:3.13.12-slim-bookworm

ENV PYTHONUNBUFFERED=1

RUN adduser --system --uid 10001 --disabled-password --no-create-home --shell /usr/sbin/nologin capuser

COPY app/sandbox/egress_proxy.py /srv/egress_proxy.py
WORKDIR /srv

USER 10001:10001
EXPOSE 8080
CMD ["python", "-u", "-c", "import asyncio; from egress_proxy import run_egress_proxy; asyncio.run(run_egress_proxy(port=8080))"]
