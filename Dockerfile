FROM docker:cli

COPY watcher.sh /usr/local/bin/watcher.sh
RUN chmod +x /usr/local/bin/watcher.sh

WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/watcher.sh"]
