# Always-on host for the team bots. The image holds only the launcher; at start it
# clones the scrabblebot branch, so code and params updates arrive by git pull
# without a redeploy.
FROM node:22-slim
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
  && rm -rf /var/lib/apt/lists/*
COPY deploy/run-all.sh /app/run-all.sh
ENV WORK_DIR=/data
CMD ["bash", "/app/run-all.sh"]
