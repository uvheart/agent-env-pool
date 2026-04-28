"""Allow running as `python -m agent_env_pool`."""

if __name__ == "__main__":
    import argparse

    import uvicorn

    from agent_env_pool.app import app

    parser = argparse.ArgumentParser(description="AgentEnvPool API Server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8100)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    uvicorn.run(app, host=args.host, port=args.port)
