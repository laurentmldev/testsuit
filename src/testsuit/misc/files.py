"""File and path helpers."""
from collections.abc import Callable
import os,hashlib,platform,subprocess,re
from colorama import init as colorama_init

def expandPath(path: str) -> str:
    """Absolute, symlink-free version of path, with '~' expanded (and cygwin paths converted on Windows)."""
    respath=os.path.expanduser(path)

    # try to robustify cygwin paths
    if platform.system().startswith("Windows"):
        respath=subprocess.run(["cygpath","-wa",respath],stdout=subprocess.PIPE,text=True).stdout.strip()
        colorama_init() # call to subprocess breaks shell colors. TODO: find better way to fix it ...

    return os.path.realpath(respath)


def toBashPath(p: str | os.PathLike) -> str:
    """Convert a Windows path to a format understood by Cygwin/Git Bash (ex: C:\\a\\b -> /cygdrive/c/a/b).
    Returned unchanged on other systems."""
    if platform.system() != "Windows":
        return str(p)
    s = str(p)
    if len(s) >= 2 and s[1] == ':':
        s = '/' + s[0].lower() + s[2:].replace('\\', '/')

    if s.startswith("/"): s="/cygdrive"+s
    return s

def getAbsFilePath(curPath: str, dirname: str | None = None) -> str:
    """Real path of curPath; a relative curPath is taken relative to dirname.

    Paths starting with os.sep, '$', '~' or 'C:' are considered absolute (they are not expanded).
    """
    if curPath[0]!=os.sep and curPath[0]!='$' and curPath[0]!='~' and not curPath.startswith("C:"):
        return os.path.realpath(dirname+os.sep+curPath)
    return os.path.realpath(curPath)

def normalizeFileName(name: str) -> str:
    """Replace whitespace and characters unsafe in file names (,/\\%@!:;<>~&#*$^) by '_'."""
    return re.sub(r"[\s,/\\%@!:;<>~&#*$^]","_",name)

# from https://stackoverflow.com/questions/1094841/get-a-human-readable-version-of-a-file-size
def sizeof_fmt(num: float, suffix: str = "B") -> str:
    """Human readable size, ex: sizeof_fmt(2048) -> '2.0KB'."""
    for unit in ("", "K", "M", "G", "T", "P", "E", "Z"):
        if abs(num) < 1024.0:
            return f"{num:3.1f}{unit}{suffix}"
        num /= 1024.0
    return f"{num:.1f}Yi{suffix}"

######################
## from https://stackoverflow.com/questions/27187490/how-to-efficiently-traverse-a-directory-and-get-the-sha256-checksum-for-each-fil
def processFolderFiles(root_path: str, processFileCb: Callable[[str, str], object]) -> bool | None:
    """Call processFileCb(file_path, root_path) on each file under root_path (recursively).

    :return: True if all files were processed, False as soon as the callback returns None or False,
        None if root_path is not readable
    """
    if not os.access(root_path,os.R_OK):
        print("ERROR: provided path not reachable for files walk: '"+str(root_path)+"'")
        return None

    for root, _dirs, files in os.walk(root_path):
        for name in files:
            file_path = os.path.join(root, name)
            if os.path.isfile(file_path):
                rst=processFileCb(file_path, root_path)
                if rst in (None, False):
                    return False

    return True

## from https://stackoverflow.com/questions/27187490/how-to-efficiently-traverse-a-directory-and-get-the-sha256-checksum-for-each-fil
def checksumFile(path: str, root_path: str, block_size: int = 4096) -> str | None:
    """Append "<path>\\t<sha256>" to the file '<root_path>.sha256'.

    :return: the checksum file path, or None if a file could not be read or written
    """
    checksumFilePath=root_path+".sha256"
    try:
        with open(path, 'rb') as rf:
            h = hashlib.sha256()
            for chunk in iter(lambda: rf.read(block_size), b''):
                h.update(chunk)

        with open(checksumFilePath, 'a') as wf:
            wf.write(path+"\t"+h.hexdigest()+"\n")

        return checksumFilePath

    except OSError:
        return None


def checksumFolder(path: str) -> bool | None:
    """Write the sha256 of every file under path into '<path>.sha256' (see processFolderFiles for the return value)."""
    return processFolderFiles(path,checksumFile)
