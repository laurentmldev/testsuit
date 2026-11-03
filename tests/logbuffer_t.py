import logging

from testsuit.sequencer.logbuffer import LogBuffer
from testsuit.sequencer.sequencer import _event_line


def test_log_buffer_lines_since_and_level():
    buffer = LogBuffer(size=3)
    logger = logging.getLogger("testsuit.test_logbuffer")
    logger.addHandler(buffer)
    logger.setLevel(logging.INFO)
    try:
        logger.info("run r1 queued")
        logger.warning("component c1 unreachable")
        try:
            raise ValueError("boom")
        except ValueError:
            logger.exception("scheduler error")
        logger.info("run r1 done")
    finally:
        logger.removeHandler(buffer)

    everything = buffer.lines()
    assert [line["n"] for line in everything["lines"]] == [2, 3, 4] and everything["next"] == 4   # the last 3 kept
    assert "Traceback" in everything["lines"][1]["message"] and "ValueError: boom" in everything["lines"][1]["message"]
    assert [line["level"] for line in buffer.lines(level="warning")["lines"]] == ["WARNING", "ERROR"]
    assert buffer.lines(since=3)["lines"][0]["message"] == "run r1 done"
    assert buffer.lines(since=4)["lines"] == []


def test_event_line():
    assert _event_line("run", {"run": "r1", "state": "done", "verdict": "passed", "reason": "", "binding": None}) == \
        "run: run=r1 state=done verdict=passed"
    assert len(_event_line("lab", {"git": {"commit": "x" * 300}})) < 200
