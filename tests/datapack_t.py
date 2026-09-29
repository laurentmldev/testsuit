import sys, os, pytest, logging, re, filecmp, difflib
from pathlib import Path

# add deps folder (relative path to this module)
sys.path.append(os.path.realpath(os.path.dirname( __file__[:-1] if __file__.endswith('.pyc') else __file__ ) +os.sep+".."+os.sep+"src"))

from testsuit.datatools.datapack.datapack_tools import datapack

logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger()

# Regex patterns to normalize dynamic values before comparison
# Each tuple: (regex_pattern, replacement_placeholder)
IGNORE_PATTERNS = [
    (r'\\','/'),
    (r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}', '<TIMESTAMP>'),
    # pwd before the generic path rule, which would otherwise eat it when the
    # checkout folder itself is named testsuit (e.g. CI: .../testsuit/testsuit)
    (r'pwd=\s*\S+', 'pwd=<PWD>'),
    (r'PWD:\s*\S+', 'PWD: <PWD>'),
    (r'(:|=).*/testsuit/',r'\1.../testsuit/'),
    (r'User:\s*\S+', 'User: <USER>'),
    (r'Host:\s*\S+', 'Host: <HOST>'),
    (r'host=\s*\S+', 'host=<HOST>'),
    (r'C:/Users/[^/]+/',r'<HOME>/'),
    (r'/home/[^/]+', '<HOME>'),
    (r'/root/[^/]+', '<ROOT_HOME>'),
    
]

def normalize_content(text, patterns):
    for pattern, replacement in patterns:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text

# Filter out empty directories from "only in ..." reports
def isEmptyFolder(path):
    if os.path.isfile(path):
        return False
    if os.path.isdir(path):
        return not bool(os.listdir(path))
    return True

def compare_dirs_recursively(left, right, patterns):
    """Recursively compare two directories, normalizing content with given patterns."""
    diffs = []
    try:
        left_items = set(os.listdir(left))
        right_items = set(os.listdir(right))
    except Exception as e:
        return [f"Error listing directories: {e}"]

    only_left = left_items - right_items
    only_right = right_items - left_items
    common = left_items & right_items

    only_left  = {f for f in only_left if not isEmptyFolder(os.path.join(left, f))}
    only_right = {f for f in only_right if not isEmptyFolder(os.path.join(right, f))}
    
    if only_left:
        diffs.append(f"Only in generated: {sorted(only_left)}")
    if only_right:
        diffs.append(f"Only in reference: {sorted(only_right)}")

    for fname in sorted(common):
        lpath = os.path.join(left, fname)
        rpath = os.path.join(right, fname)

        if os.path.isdir(lpath) and os.path.isdir(rpath):
            diffs.extend(compare_dirs_recursively(lpath, rpath, patterns))
        elif os.path.isfile(lpath) and os.path.isfile(rpath):
            # Fast path: skip normalization if files are already byte-identical
            if filecmp.cmp(lpath, rpath, shallow=False):
                continue
                
            try:
                with open(lpath, 'r', encoding='utf-8', errors='replace') as f:
                    l_content = f.read()
                with open(rpath, 'r', encoding='utf-8', errors='replace') as f:
                    r_content = f.read()

                l_norm = normalize_content(l_content, patterns)
                r_norm = normalize_content(r_content, patterns)

                if l_norm != r_norm:
                    diff = difflib.unified_diff(
                        r_norm.splitlines(keepends=True),
                        l_norm.splitlines(keepends=True),
                        fromfile=f"ref/{fname}",
                        tofile=f"gen/{fname}",
                        lineterm="",
                    )
                    diffs.append(f"Content differs in: {fname}\n{''.join(diff)}")
            except Exception as e:
                diffs.append(f"Error comparing {fname}: {e}")

    return diffs

@pytest.mark.parametrize(
    "testdef_file,ignore_checks,reference_folder",
    [
        ("tests/etc/datapack/testdef/datapack_utest.yml", True, "tests/ref/datapack_test/datapack_utest"),
    ]
)
def test_datapack_nominal(testdef_file, ignore_checks, reference_folder):

    # needed so that pytest handle some utf8 chars written by datapack tool
    os.environ["PYTHONIOENCODING"]="utf-8"
    
    # create datapack
    assert(datapack(testdef_file=testdef_file, nocheck=ignore_checks))

    # verify .current_datapack is recursively identical to the reference folder
    generated = Path(testdef_file).parent / ".current_datapack"
    assert os.path.isdir(generated), f"Generated folder '{generated}' not found"
    assert os.path.isdir(reference_folder), f"Reference folder '{reference_folder}' not found"

    diffs = compare_dirs_recursively(generated, reference_folder, IGNORE_PATTERNS)
    assert not diffs, f"Generated folder differs from reference:\n" + "\n".join(diffs)