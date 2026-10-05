"""Replace key references by their value, taken from a dictionary.

A key reference is ``_K_(name)`` or ``<_key_ src="name"/>``. A value can itself contain key
references, and a key name can be built from other keys (``_K_(sensor._K_(id).unit)``), so the
replacement is repeated, run after run, as long as some key was replaced. A key name cannot
contain parentheses: in a nested reference, the innermost key is replaced first.

Undefined keys are marked ``_K_?(name)`` and circular references ``_K_!(name)``.

Usage::

    setDico(dico, keysorigin)                      # replacement context
    lines, usedkeys, undefinedKeys = replaceKeys(fileName, lines)
    text = [finalizeLine(line) for line in lines]

Each replaced value is wrapped as ``_rpl_{key}__value__rpl_``, so that the origin of every piece of
text can be traced (see the HTML view built by evalfile). finalizeLine() removes this wrapping.

The ``nbUndefined`` and ``nbInfinateRecursion`` counters add up over replaceKeys() calls until
resetCounters().
"""

import argparse
import functools
import getpass
import os
import re
import socket
import sys
from collections import defaultdict
from pathlib import Path

from testsuit.datatools.datapack.evalfiles import libdictionary

## max runs before arbitrary stop in case of undetected cyclic reference
MAX_RUNS = 15

# The string used to detect the key refs, i.e. "_K_" in "_K_(myKey)"
KEYMARK = "_K_"
XMLMARK = "_key_"
# '_K_(name)' or '<_key_ src="name"/>', the key name being group MATCH_GROUP
KEY_REGEX = r"(\<" + XMLMARK + " src=(\"|\')|" + KEYMARK + r'\(' + r')\s*([^()]+?)\s*(\)|(\"|\')\s*\/\>)'
MATCH_GROUP = 3
KEY_PATTERN = re.compile(KEY_REGEX, re.IGNORECASE)
# a value starting with a key ref: it is not final yet
MATCH_KEYREF_MARKER = re.compile(KEYMARK + r"\(([^)]+)")
UNKNOWN_KEY_MARKER = "?"
CYCLIC_KEY_MARKER = "!"

# a replaced value is inserted as '_rpl_{key}__value__rpl_', see finalizeLine
RPL_MARKER = "_rpl_"
FINALIZE_RPL_START = re.compile(RPL_MARKER + r"\{[^}]+\}__")
RPL_MATCH_REGEX = RPL_MARKER + r"\{([^}]+)\}__(.*?)__" + RPL_MARKER
NEW_LINE_MARKER = "__CR__"

## replacement context, see setDico
dico = {}
## origin (dictionary file) of the keys
keysorigin = {}

## number of undefined keys met (when not ignored)
nbUndefined = 0
## number of circular references met
nbInfinateRecursion = 0


def resetCounters():
    """Reset the nbUndefined and nbInfinateRecursion counters."""
    global nbUndefined, nbInfinateRecursion
    nbUndefined = 0
    nbInfinateRecursion = 0


def getUndefinedKeysStr(undefinedKeysData):
    """Error message listing the undefined keys returned by replaceKeys, with their positions."""
    return "\n".join(
        f"undefined key '{key}' :" + "".join(f"\n\t-> {where['file']}:{where['position']}" for where in places)
        for key, places in undefinedKeysData.items())


def finalizeLine(line):
    """Remove the replacement marks of a line returned by replaceKeys, giving the final text."""
    return FINALIZE_RPL_START.sub("", line).replace("__" + RPL_MARKER, "").replace(NEW_LINE_MARKER, "\n").rstrip()


def replaceKeys(filePath, lines, doIgnoreMissing=False):
    """Replace the keys of the given lines (in place) by their value in the dictionary set with setDico.

    Returned lines still carry replacement marks: see finalizeLine.

    :param filePath: name of the processed file, for messages only
    :param lines: lines to process
    :param doIgnoreMissing: leave undefined keys as they are (used when the keys may be defined later on,
        e.g. by an include). Otherwise they are marked as undefined and counted in nbUndefined.
    :return: lines, {used key: origin}, {undefined key: [{'file','position'}]}
    """
    usedkeys = {}
    undefinedKeys = {}
    # keys replaced so far at each 'line:column', to detect circular references
    replacedAt = defaultdict(list)
    nbReplaced = 0

    def substitute(match, lineNb):
        global nbUndefined, nbInfinateRecursion
        nonlocal nbReplaced
        # the key name may hold values of keys already replaced inside it
        keyname = finalizeLine(match.group(MATCH_GROUP))
        position = f"{lineNb}:{match.start()}"
        replacedHere = replacedAt[position]

        if keyname in replacedHere:
            print(f"WARNING: {filePath}:{position}\t: infinate recursion during replacement. Keys are {replacedHere}")
            nbInfinateRecursion += 1
            return f"{KEYMARK}{CYCLIC_KEY_MARKER}({keyname})"

        if keyname not in dico:
            undefinedKeys.setdefault(keyname, []).append({'file': filePath, 'position': position})
            if doIgnoreMissing:
                return f"{KEYMARK}({keyname})"
            nbUndefined += 1
            return f"{KEYMARK}{UNKNOWN_KEY_MARKER}({keyname})"

        replacedHere.append(keyname)
        nbReplaced += 1
        usedkeys[keyname] = keysorigin.get(keyname, "")
        value = dico[keyname]
        # a value which is still a key ref is marked when its own key gets replaced
        if MATCH_KEYREF_MARKER.match(value):
            return value
        return f"{RPL_MARKER}{{{keyname}}}__{value}__{RPL_MARKER}"

    for _ in range(MAX_RUNS + 1):
        nbReplaced = 0
        for idx, line in enumerate(lines):
            lines[idx] = KEY_PATTERN.sub(functools.partial(substitute, lineNb=idx + 1), line)
        if nbReplaced == 0:
            break

    return lines, usedkeys, undefinedKeys


def _getDefaultKeysDico():
    """Environment keys, available in any replacement: dico, origins."""
    defaultKeysDico = {
        "_ENV_USER_": getpass.getuser(),
        "_ENV_HOME_": str(Path.home()),
        "_ENV_USERID_": getpass.getuser(),
        "_ENV_PWD_": os.getenv("PWD", ""),
        "_ENV_HOSTNAME_": socket.gethostname(),
        "_ENV_TIMESTAMP_": libdictionary.getTimestamp(),
    }
    return defaultKeysDico, dict.fromkeys(defaultKeysDico, os.path.realpath(__file__))


def setDico(replaceDictionary, mykeysorigin=None):
    """Set the replacement context used by replaceKeys: the given keys plus a few environment keys."""
    defaultKeysDico, defaultKeysOrigin = _getDefaultKeysDico()
    # replaceDictionary may be the current dico itself (see getDico)
    newDico = {**replaceDictionary, **defaultKeysDico}
    dico.clear()
    dico.update(newDico)
    keysorigin.update(mykeysorigin or {})
    keysorigin.update(defaultKeysOrigin)


def getDico():
    """Current replacement context: dico, keysorigin."""
    return dico, keysorigin


## check if the given file is accessible
def _isInputFileReadable(f):
    if not os.access(f, os.R_OK):
        raise argparse.ArgumentTypeError(f"{f} does not exist or is not readable")
    return f


# override the parsing error message using logger
class _HelpParser(argparse.ArgumentParser):
    def error(self, message):
        print("Input Arguments Error : " + message)
        sys.exit(1)


def main():
    parser = _HelpParser(description=
"""Replace in the given file all """ + KEYMARK + """(keyname) by the corresponding value found in given dictionary (declared as "keyname=this is replacement text").

This tool can safely handle undefined keys and most of circular infinite recursions.

Result lines are displayed in <stdout>.

Return:
	2 if circular references detected
	1 if undefined reference detected (and not circular references)
	0 if okay""",
    formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument('targetfile', help="the text file where to replace keys", type=_isInputFileReadable, metavar="targetfile")
    parser.add_argument('dicos', nargs='+', help="dictionary file(s) to be used, most important one first", type=_isInputFileReadable)
    parser.add_argument('--ignoreundef', action='store_true', help="ignore undefined keys")
    args = parser.parse_args()

    with open(args.targetfile) as f:
        lines = f.readlines()

    setDico(*libdictionary.loadDicos(args.dicos))
    rlines, _, undefinedKeys = replaceKeys(args.targetfile, lines, args.ignoreundef)

    if undefinedKeys and not args.ignoreundef:
        print(getUndefinedKeysStr(undefinedKeys))

    for line in rlines:
        sys.stdout.write(finalizeLine(line) + "\n")
    sys.stdout.write("\n")

    if nbInfinateRecursion > 0:
        sys.exit(2)
    if nbUndefined > 0 and not args.ignoreundef:
        sys.exit(1)
    sys.exit(0)


if __name__ == '__main__':
    main()
