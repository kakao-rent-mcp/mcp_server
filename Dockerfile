# syntax=docker/dockerfile:1
FROM python:3.12-slim AS builder

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY README.md ./
RUN uv sync --frozen --no-dev

FROM python:3.12-slim

# 공공데이터포털 서비스키. KC는 런타임 환경변수 주입을 지원하지 않으므로
# CI 빌드 시 --build-arg로 주입해 이미지에 구워 넣는다.
# 주의: 이렇게 하면 키가 이미지 레이어에 평문으로 남는다. 따라서 이 이미지는
# 반드시 '비공개' 레지스트리에만 올려야 한다 (소스 레포 공개와 무관).
ARG DECODING_KEY=""
ARG ENCODING_KEY=""

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src

ENV PATH="/app/.venv/bin:$PATH" \
    MCP_TRANSPORT=streamable-http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8000 \
    DECODING_KEY=$DECODING_KEY \
    ENCODING_KEY=$ENCODING_KEY

EXPOSE 8000

CMD ["slug-mcp"]
