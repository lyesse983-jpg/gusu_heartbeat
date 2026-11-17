FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --create-home --uid 10001 companion \
    && mkdir -p /app/data \
    && chown companion:companion /app/data
COPY --chown=companion:companion companion ./companion
USER companion
EXPOSE 8765
CMD ["python", "-m", "companion"]
