FROM python:3.13-slim
# Node is only for the Claude Code CLI (used as an LLM lane when CLAUDE_CODE_OAUTH_TOKEN is set)
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm ca-certificates && rm -rf /var/lib/apt/lists/* \
 && npm install -g @anthropic-ai/claude-code && npm cache clean --force
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app app
COPY scripts scripts
COPY seed seed
COPY start.sh .
ENV PYTHONUNBUFFERED=1
CMD ["sh", "start.sh"]
