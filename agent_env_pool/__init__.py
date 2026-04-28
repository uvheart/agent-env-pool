"""AgentEnvPool: parallel sandbox environments for agent rollout."""

__version__ = "0.1.0"
from agent_env_pool.client import EnvHandle, EnvPoolClient

__all__ = ["EnvHandle", "EnvPoolClient"]
