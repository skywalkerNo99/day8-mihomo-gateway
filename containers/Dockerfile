# Optional Linux packaging/isolated validation; never a macOS deployment dependency.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY gateway requirements.txt ./
COPY scripts/mihomo-lock.json scripts/mihomo-lock.json
RUN python3 gateway bootstrap
RUN apt-get update && apt-get install -y --no-install-recommends iptables iproute2 \
    && rm -rf /var/lib/apt/lists/*
COPY src src
COPY rules rules
RUN mkdir -p secrets .local && chmod 700 .local
ENTRYPOINT ["python3", "gateway"]
CMD ["run", "--bind", "0.0.0.0"]
