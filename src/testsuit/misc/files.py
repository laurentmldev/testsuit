
from collections.abc import Callable
import os,hashlib,platform,subprocess,re
from colorama import init as colorama_init

def expandPath(path: str) -> str:

    respath=os.path.expanduser(path)

    # try to robustify cygwin paths
    if platform.system().startswith("Windows"): 
        respath=subprocess.Popen("cygpath -wa '"+respath+"'", shell=True,stdout=subprocess.PIPE).stdout.read().decode().strip()
        #subprocess.run("echo ''",shell=True,stdout=subprocess.PIPE)
        colorama_init() # call to subprocess breaks shell colors. TODO: find better wau to fix it ...

    respath=os.path.realpath(respath)
    #print("         2> "+respath)

    return respath


def toBashPath(p: str | os.PathLike) -> str:
    """Convert a Windows path to a format understood by Git Bash/MSYS2."""
    if platform.system() != "Windows":
        return str(p)
    s = str(p)
    if len(s) >= 2 and s[1] == ':':
        s = '/' + s[0].lower() + s[2:].replace('\\', '/')
        
    if s.startswith("/"): s="/cygdrive"+s
    return s

def getAbsFilePath(curPath: str, dirname: str | None = None) -> str:
    """Return real path, combine it with provided dirname if path is relative"""
    if curPath[0]!=os.sep and curPath[0]!='$' and curPath[0]!='~' and not curPath.startswith("C:"):
        return os.path.realpath(dirname+os.sep+curPath)
    return os.path.realpath(curPath)

def normalizeFileName(name: str) -> str:
    return re.sub(r"[\s,/\\%@!:;<>~&#*$^]","_",name)

# from https://stackoverflow.com/questions/1094841/get-a-human-readable-version-of-a-file-size
def sizeof_fmt(num: float, suffix: str = "B") -> str:
    for unit in ("", "K", "M", "G", "T", "P", "E", "Z"):
        if abs(num) < 1024.0:
            return f"{num:3.1f}{unit}{suffix}"
        num /= 1024.0
    return f"{num:.1f}Yi{suffix}"

######################
## from https://stackoverflow.com/questions/27187490/how-to-efficiently-traverse-a-directory-and-get-the-sha256-checksum-for-each-fil
def processFolderFiles(root_path: str, processFileCb: Callable[[str, str], bool]) -> bool | None:
    """Execute callback(file_path, root_path) func for each file under given folder (recursively)
    CB must return True for process to continue"""
    directories = []
    files = []
    if not os.access(root_path,os.R_OK):
        print("ERROR: provided path not reachable for files walk: '"+str(root_path)+"'")
        return None
    
    for root, dirs_o, files_o in os.walk(root_path):
        
        for name in dirs_o:
            directories.append(os.path.join(root, name))

        for name in files_o:            
            file_path = os.path.join(root, name)
            if os.path.isfile(file_path):                
                rst=processFileCb(file_path, root_path)                 
                if rst==None or rst==False:
                    return False

    # return last rst
    return True

## from https://stackoverflow.com/questions/27187490/how-to-efficiently-traverse-a-directory-and-get-the-sha256-checksum-for-each-fil
def checksumFile(path: str, root_path: str, block_size: int = 4096) -> str | None:

    checksumFile=root_path+".sha256"
    try:
        with open(path, 'rb') as rf:
            h = hashlib.sha256()
            for chunk in iter(lambda: rf.read(block_size), b''):
                h.update(chunk)

        with open(checksumFile, 'a') as wf:
            wf.write(path+"\t"+str(h.hexdigest()+"\n"))

        return checksumFile
    
    except OSError:
        return None


def checksumFolder(path: str) -> bool | None:
    return processFolderFiles(path,checksumFile)
