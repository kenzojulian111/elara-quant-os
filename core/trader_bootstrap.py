import asyncio
import os


class TraderBootstrap:
    @staticmethod
    def _prepare_env():
        os.environ.setdefault("APP_ROLE", "trader")
        os.environ.setdefault("ENABLE_TRADING_SKILL", "0")
        os.environ.setdefault("ENABLE_TRADING_ORCHESTRATOR", "0")

    async def run(self):
        self._prepare_env()
        from skills.advanced.trading.daemon_runner import main as run_trader
        await run_trader()


def main():
    bootstrap = TraderBootstrap()
    asyncio.run(bootstrap.run())


if __name__ == "__main__":
    main()

