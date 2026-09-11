FROM python:3.12-alpine

COPY watcher.py /usr/local/bin/watcher.py
RUN chmod +x /usr/local/bin/watcher.py

WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/watcher.py"]
