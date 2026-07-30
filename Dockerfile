FROM --platform=linux/arm64 python:3.11-slim AS builder

WORKDIR /app
COPY requirements-agent.txt ./requirements.txt
RUN pip install --no-cache-dir --target=/app/deps -r requirements.txt

FROM --platform=linux/arm64 python:3.11-slim

ARG AGENT_FILE

WORKDIR /app

COPY --from=builder /app/deps /usr/local/lib/python3.11/site-packages/
RUN python -m compileall /usr/local/lib/python3.11/site-packages/ -q 2>/dev/null || true

COPY ${AGENT_FILE} ./agent.py

ENV DOCKER_CONTAINER=true
EXPOSE 9000

CMD ["python", "agent.py"]
