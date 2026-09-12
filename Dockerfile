FROM python:3.12-alpine

RUN apk add --no-cache docker-cli docker-cli-compose

COPY watcher.py /usr/local/bin/watcher.py
RUN chmod +x /usr/local/bin/watcher.py

WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/watcher.py"]
