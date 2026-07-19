# Edge gateway image — SKU-1 standalone. Builds from shared/ + edge/ ONLY.
# It never copies center/, which is what makes "deploy just the edge" true.
# (tools/check_boundaries.py enforces the source-level version of this rule.)
FROM python:3.12-slim
WORKDIR /app

# 1) the shared wire contract (dependency-free), installed as a package
COPY shared/ /build/shared/
RUN pip install --no-cache-dir /build/shared

# 2) edge deps + code
COPY edge/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY edge/gateway/ /app/gateway/

CMD ["python", "-u", "-m", "gateway.main"]
