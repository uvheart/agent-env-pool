FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN python -m pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple --upgrade pip setuptools wheel \
    && pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

COPY agent_env_pool/ /app/agent_env_pool/

EXPOSE 8100

CMD ["python", "-m", "agent_env_pool", "--host", "0.0.0.0", "--port", "8100"]
