FROM python:3.12-slim
WORKDIR /app
COPY server.py /app/server.py
ENV DATA_DIR=/data PORT=8080
EXPOSE 8080
CMD ["python3", "/app/server.py"]
