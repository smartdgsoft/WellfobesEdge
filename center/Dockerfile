# Central historian subscriber. Part of SKU-2 (edge + center).
FROM python:3.12-slim
WORKDIR /app
COPY shared/ /build/shared/
RUN pip install --no-cache-dir /build/shared
COPY center/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY center/historian/ /app/historian/
COPY center/config_service/ /app/config_service/
CMD ["python", "-u", "-m", "historian.main"]
