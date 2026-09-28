FROM python:3.12-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
RUN groupadd --system app && useradd --system --gid app --home-dir /app app
COPY requirements.txt requirements-test.txt ./

FROM base AS runtime
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
USER app
EXPOSE 8081
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8081"]

FROM base AS test
RUN pip install --no-cache-dir -r requirements-test.txt
COPY . .
RUN chown -R app:app /app
USER app
CMD ["python", "-m", "pytest", "-q"]
