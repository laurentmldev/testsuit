"""Process-wide logger shared by the testsuit tools.

create_logger() configures a named logger (console and/or file output, each with its own format
and level) and registers it as the main logger that get_logger() returns everywhere.
Adapted from https://stackoverflow.com/questions/58965871/logger-that-logs-to-console-and-file-at-different-levels
"""
import logging
import os,sys
import pathlib
from pathlib import Path


MAIN_LOGGER_INST=None

# Ready-made configurations for create_logger(). Copy them before changing a value:
# they are shared by every caller.
DEFAULT_CONFIG_CONSOLE_ONLY={"consoleLevel":logging.INFO,
                    "consoleFormat":'%(asctime)s [%(name)s] [%(levelname)1s] %(funcName)s - %(message)s',
                    "consoleDateFormat":"%Y-%m-%d %H:%M:%S"
                }

DEFAULT_CONFIG_FILE_ONLY={"fileLevel":logging.DEBUG,
                    "fileFormat":'%(asctime)s;%(name)s;%(levelname)1s;%(funcName)s;%(message)s',
                    "fileDateFormat":"%Y-%m-%d %H:%M:%S",
                    "fileMode":"a",
                    "fileFolder":str(Path.home())+os.sep+"logs"
                }

DEFAULT_CONFIG_DUAL=DEFAULT_CONFIG_CONSOLE_ONLY | DEFAULT_CONFIG_FILE_ONLY


def get_logger() -> logging.Logger:
    """Return the main logger.

    :raises Exception: if create_logger() was never called
    """
    if MAIN_LOGGER_INST is None:
        raise Exception("no logger defined!")

    return MAIN_LOGGER_INST

def _close_handlers(logger: logging.Logger) -> None:
    """Close and detach all handlers of given logger (a closed FileHandler would otherwise reopen its file on next log)."""
    for handler in logger.handlers:
        handler.close()
    logger.handlers=[]

def reset_logger(logger: logging.Logger | None = None) -> None:
    """Close the current main logger's handlers, then make given logger (if any) the main logger."""
    global MAIN_LOGGER_INST
    if MAIN_LOGGER_INST is not None:
        _close_handlers(MAIN_LOGGER_INST)
        MAIN_LOGGER_INST=None
    if logger:
        MAIN_LOGGER_INST=logger

def create_logger(
    name: str = __name__,
    filename: str | None = None,
    config: dict | None = None,
    reset: bool = False
) -> logging.Logger:
    """Configure logger 'name' with console and/or file output.

    :param name: logger name (shown in console messages)
    :param filename: log file name ('.log' is appended if missing), written in config["fileFolder"]
        (created if needed). None disables file logging.
    :param config: a DEFAULT_CONFIG_* dict, or a dict with the same keys. Console output is enabled
        when it has "consoleFormat". Default: DEFAULT_CONFIG_DUAL with a filename, else DEFAULT_CONFIG_CONSOLE_ONLY.
    :param reset: make this logger the main one even if one already exists
    :return: the configured logger; it also becomes the main logger (see get_logger())
        if none was defined yet or reset is True
    """
    global MAIN_LOGGER_INST

    logger = logging.getLogger(name)
    # logger level stays the lowest, each handler filters with its own level
    logger.setLevel(logging.DEBUG)

    if config is None:
        config=DEFAULT_CONFIG_DUAL if filename is not None else DEFAULT_CONFIG_CONSOLE_ONLY
    config=dict(config) # never modify caller's (possibly shared) dict

    # logging.getLogger() returns the same object for the same name: drop previous handlers
    _close_handlers(logger)

    # StreamHandler - logging config for console
    if "consoleFormat" in config:
        sh = logging.StreamHandler(stream=sys.stdout)
        sh.setFormatter(logging.Formatter(config["consoleFormat"]))
        sh.formatter.datefmt = config["consoleDateFormat"]
        sh.setLevel(config["consoleLevel"])
        logger.handlers.append(sh)

    # FileHandler - logging into file
    if filename is not None:
        if config.get("fileFolder"):
            pathlib.Path(config["fileFolder"]).mkdir(parents=True, exist_ok=True)
        else:
            config["fileFolder"]=""

        if not filename.endswith(".log"):
            filename+=".log"

        fileFullPath=config["fileFolder"]+os.sep+filename
        fh = logging.FileHandler(fileFullPath, mode=config["fileMode"])
        fh.setLevel(config["fileLevel"])
        fh.setFormatter(logging.Formatter(config["fileFormat"]))
        fh.formatter.datefmt = config["fileDateFormat"]
        logger.handlers.append(fh)
        print("[logging to file '"+os.path.abspath(fileFullPath)+"']")

    if reset or MAIN_LOGGER_INST is None:
        MAIN_LOGGER_INST=logger

    return logger
