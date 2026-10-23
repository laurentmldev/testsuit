"""tsqd: the sequencer daemon. One process, one asyncio loop: the REST API (uvicorn), the scheduler,
the state monitor and the retention cleanup (README sections 9 to 13)."""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import sys

from testsuit.sequencer.config import load_config
from testsuit.sequencer.util import SequencerError

log = logging.getLogger("testsuit.sequencer")


async def housekeeping(seq) -> None:
    while True:
        try:
            seq.cleanup()
        except Exception:
            log.exception("retention cleanup")
        await asyncio.sleep(3600)


def build(config_path: str | None = None):
    """The sequencer and its FastAPI app, background loops started with the app (lifespan)."""
    from testsuit.sequencer.api import create_app
    from testsuit.sequencer.sequencer import Sequencer, load_config_plugins

    config = load_config(config_path)
    load_config_plugins(config)
    seq = Sequencer(config)

    @contextlib.asynccontextmanager
    async def lifespan(app):
        await seq.recover()
        tasks = [asyncio.ensure_future(seq.scheduler_loop()), asyncio.ensure_future(seq.monitor.loop()),
                 asyncio.ensure_future(housekeeping(seq))]
        log.info(f"tsqd: lab {seq.lab.folder} ({(seq.lab.git or {}).get('commit') or 'no git'}), "
                 f"storage {config.root}")
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            # active runs: their processes are killed, finally runs and results are packed (as on cancel)
            for execution in list(seq.executions.values()):
                await execution.abort("kill")
            pending = [e.task for e in seq.executions.values() if e.task]
            await asyncio.gather(*pending, return_exceptions=True)
            await seq.close()

    return seq, create_app(seq, lifespan)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="tsqd", description="testsuit sequencer daemon (REST API on server.listen)")
    parser.add_argument("--config", help="tsq.yml (default: $TSQ_CONFIG, then /etc/testsuit/tsq.yml)")
    parser.add_argument("--listen", help="host:port, instead of server.listen")
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    import uvicorn
    try:
        seq, app = build(args.config)
    except SequencerError as e:
        print(f"tsqd: {e}", file=sys.stderr)
        return 2
    host, _, port = (args.listen or seq.config.server.listen).rpartition(":")
    uvicorn.run(app, host=host or "127.0.0.1", port=int(port), log_level=args.log_level.lower())
    return 0


if __name__ == "__main__":
    sys.exit(main())
