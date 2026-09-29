
  
# from https://stackoverflow.com/questions/58965871/logger-that-logs-to-console-and-file-at-different-levels
import logging
import os,sys
import pathlib
from pathlib import Path



MAIN_LOGGER_INST=None

DEFAULT_CONFIG_CONSOLE_ONLY={"consoleLevel":logging.INFO,\
                    "consoleFormat":'%(asctime)s [%(name)s] [%(levelname)1s] %(funcName)s - %(message)s',\
                    "consoleDateFormat":"%Y-%m-%d %H:%M:%S"                
                }

DEFAULT_CONFIG_FILE_ONLY={"fileLevel":logging.DEBUG,\
                    "fileFormat":'%(asctime)s;%(name)s;%(levelname)1s;%(funcName)s;%(message)s',\
                    "fileDateFormat":"%Y-%m-%d %H:%M:%S",\
                    "fileMode":"a",\
                    "fileFolder":str(Path.home())+os.sep+"logs"                  
                }

DEFAULT_CONFIG_DUAL=DEFAULT_CONFIG_CONSOLE_ONLY | DEFAULT_CONFIG_FILE_ONLY        
                


def get_logger() -> logging.Logger:
    global MAIN_LOGGER_INST
    if MAIN_LOGGER_INST==None:
        raise Exception("no logger defined!")
        
    return MAIN_LOGGER_INST

def reset_logger(logger: logging.Logger | None = None) -> None:
    global MAIN_LOGGER_INST
    if MAIN_LOGGER_INST!=None:
        for handler in MAIN_LOGGER_INST.handlers:
            handler.close()
        del MAIN_LOGGER_INST
        MAIN_LOGGER_INST=None    
    if logger:
        MAIN_LOGGER_INST=logger

def create_logger(
    name: str = __name__,
    filename: str | None = None,
    config: dict | None = None,
    reset: bool = False
) -> logging.Logger:
    """Get a dual console/file logger with individual Format and Level for console and file"""    
            
    global MAIN_LOGGER_INST

    logger = logging.getLogger(name)
    # set logger.setLevel as lowest, if handle multiple handlers independently
    logger.setLevel(logging.DEBUG)   # have higher priority than handlers level
    
    if config==None: 
        if filename!=None:        
            config=DEFAULT_CONFIG_DUAL
        else:
            config=DEFAULT_CONFIG_CONSOLE_ONLY

    # StreamHandler - logging config for console
    if "consoleFormat" in config:
        sh = logging.StreamHandler(stream=sys.stdout)
        sh.setFormatter(logging.Formatter(config["consoleFormat"]))
        sh.formatter.datefmt = config["consoleDateFormat"]
        sh.setLevel(config["consoleLevel"])
        logger.handlers = [sh]

        
    # FileHandler - logging into file
    fileFullPath=None
    if filename!=None:
        if "fileFolder" in config and config["fileFolder"]!="":
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

       
    if reset or MAIN_LOGGER_INST==None:
        MAIN_LOGGER_INST=logger

    return logger