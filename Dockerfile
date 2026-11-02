FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml ./
COPY requirements.runtime.txt ./
COPY sbsm ./sbsm
COPY scripts ./scripts
RUN pip install -r requirements.runtime.txt && pip install --no-deps . && groupadd --gid 10001 sbsm && useradd --uid 10001 --gid sbsm --create-home sbsm && mkdir -p /app/data && chown -R sbsm:sbsm /app
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=20s --timeout=5s --start-period=60s --retries=3 CMD ["python", "-m", "sbsm.healthcheck"]
CMD ["python", "-m", "sbsm.service"]
