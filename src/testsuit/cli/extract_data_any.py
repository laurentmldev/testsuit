#
# This script interprets raw values from given "any" (bytes) as proper eng. values
# Intended to be called with testsuit libs for data loading
#
#  PYTHONPATH=.../testsuit/src python main.py
#

import sys, os, argparse, re, json
from functools import partial

import pandas as pd


from testsuit.misc.logger import create_logger, get_logger
from testsuit.misc.MonitorProgress import MonitorProgress, consoleRichProgressCb

from testsuit.datatools.datatoolbox import loadDataframeFromFile
from testsuit.datatools.DataframeToHdf5 import DataframeToHdf5

from pathlib import Path

create_logger("extract_data_any")

def load_extraction_config(path):
    """Charge la config JSON """
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
        try:
            import yaml
            return yaml.safe_load(content)
        except ImportError:
            return json.loads(content)

def _shift_ascii_chars(s: str, shift: int) -> str:
    """Décale les caractères alphabétiques de `shift` positions vers le bas. Les chiffres restent inchangés."""
    if shift == 0:
        return s
    return ''.join(chr(ord(c) - shift) if c.isalpha() else c for c in s)

def swap_bytes_32(val: int) -> int:
    """Inverse l'ordre des octets d'un entier 32 bits (Big <-> Little Endian)."""
    return int.from_bytes((val & 0xFFFFFFFF).to_bytes(4, byteorder='big'), byteorder='little')

def swap_bytes_64(val: int) -> int:
    """Inverse l'ordre des octets d'un entier 64 bits (Big <-> Little Endian)."""
    return int.from_bytes((val & 0xFFFFFFFFFFFFFFFF).to_bytes(8, byteorder='big'), byteorder='little')

def parse_hex_u64_array(hex_str: str, swap_needed: bool, factor: float, ascii_shift: int = 0) -> list[float]:
    """
    Extrait des u64 depuis une chaîne hex, applique un décalage ASCII si nécessaire,
    swap les octets si demandé, et applique un facteur multiplicatif.
    """

    clean = str(hex_str).strip().lower()
    if clean.startswith("0x"):
        clean = clean[2:]

    if ascii_shift != 0:
        clean = _shift_ascii_chars(clean, ascii_shift)

    if len(clean) == 0 or len(clean) % 16 != 0:
        return []
        
    num_u64 = len(clean) // 16
    results = []
    
    for i in range(num_u64):
        chunk = clean[i*16 : (i+1)*16]
        try:
            b = bytes.fromhex(chunk)
            byteorder = 'little' if swap_needed else 'big'
            val = int.from_bytes(b, byteorder=byteorder)
            results.append(val * factor)
        except ValueError:
            # Si un chunk n'est pas hex valide, on remplit avec NaN pour garder l'alignement
            results.append(float('nan'))
            
    return results

def cbExtractBitFields(df, monitorProgress, extractions_config, sourceFolderOrFile=None, debug=False):
    """
    Extrait des champs de bits d'un entier 32 bits stocké en string,
    OU des tableaux de u64 hexadécimaux selon la config.
    """
    # Handle both Series and single-column DataFrame
    if isinstance(df, pd.DataFrame):
        if df.shape[1] != 1:
            raise ValueError("Expected a Series or a single-column DataFrame")
        base_name = df.columns[0]
        df = df.iloc[:, 0]
    else:
        base_name = df.name if df.name else 'value'
    
    # Ensure df type is string
    df = df.astype(str)
    
    # 🔍 Match dynamique : cherche la règle correspondant au nom du paramètre
    matched_rule = None
    for rule in extractions_config:
        pattern = rule.get('pattern', '')
        if re.search(pattern, base_name):
            matched_rule = rule
            break
            
    if not matched_rule:
        if debug:
            print(f"[SKIP] Aucune règle config ne correspond à '{base_name}', ignoré.")
        return []
    
    # --- BRANCHE HEX U64 ---
    if matched_rule.get('is_hex_u64', False):
        swap_needed = matched_rule.get('swap_needed', False)
        factor = matched_rule.get('factor', 1.0)
        ascii_shift = matched_rule.get('ascii_shift', 0) 

        # Déterminer le nombre de u64 sur la première valeur valide
        num_u64 = 0
        for raw_val in df:
            clean = str(raw_val).strip().lower()
            if clean.startswith("0x"): clean = clean[2:]
            if len(clean) > 0 and len(clean) % 16 == 0:
                num_u64 = len(clean) // 16
                break
        
        if num_u64 == 0:
            if debug:
                print(f"[SKIP] '{base_name}' ne contient pas de données hex u64 valides.")
            return []
            
        # Préparer les DataFrames de sortie : data_0, data_1, ...
        result_dfs = []
        for i in range(num_u64):
            col_name = f"{base_name}_{i}"
            curDf = pd.DataFrame(index=df.index.copy(), columns=[col_name], dtype="float64")
            curDf.name = col_name
            curDf.origin = sourceFolderOrFile
            result_dfs.append(curDf)
            
        monitorProgress.set_total_items(len(df))
        progress_chunk_size = max(1, len(df) // 100)
        nbItems = 1
        
        for timestamp, raw_val in df.items():
            vals = parse_hex_u64_array(raw_val, swap_needed, factor, ascii_shift)
            if len(vals) == num_u64:
                for i, v in enumerate(vals):
                    result_dfs[i].loc[timestamp] = v
                    
            nbItems += 1
            if nbItems == progress_chunk_size:
                monitorProgress.complete_n(nbItems)
                nbItems = 0
                
        monitorProgress.complete_all()
        return result_dfs

    # --- BRANCHE BIT FIELDS (u32) EXISTANTE ---
    bit_fields_config = matched_rule.get('bit_fields', [])
    swap_needed = matched_rule.get('swap_needed', False)
    
    if not bit_fields_config:
        if debug:
            print(f"[SKIP] Règle pour '{base_name}' sans bit_fields, ignoré.")
        return []
    
    # Validate config
    for field in bit_fields_config:
        if 'name' not in field or 'start_bit' not in field or 'size' not in field:
            raise ValueError(f"Each field config must have 'name', 'start_bit', and 'size': {field}")
        if field['start_bit'] + field['size'] > 32:
            raise ValueError(f"Field '{field['name']}' exceeds 32 bits: start={field['start_bit']}, size={field['size']}")
    
    monitorProgress.set_total_items(len(df))
    progress_chunk_size = max(1, len(df) // 100)
    
    # Prepare result dataframes
    result_dfs = []
    for field in bit_fields_config:
        curDf = pd.DataFrame(
            index=df.index.copy(),
            columns=[field['name']],
            dtype="uint32"
        )
        curDf.name = f"{base_name}_{field['name']}"
        curDf.origin = sourceFolderOrFile
        result_dfs.append(curDf)

    # Détecter le type de données pour adapter la conversion
    is_object_dtype = df.dtype == 'object'

    # Process each value
    nbItems = 1
    for timestamp, raw_val in df.items():
        try:
            if is_object_dtype:
                # Pour les colonnes object, on essaie plusieurs formats
                if isinstance(raw_val, (int)):
                    int_val = int(raw_val)
                elif isinstance(raw_val, str):
                    raw_val = raw_val.strip()
                    if raw_val.startswith(('0x', '0X')):
                        int_val = int(raw_val, 16)
                    else:
                        int_val = int(raw_val)
                elif isinstance(raw_val, bytes):
                    int_val = int.from_bytes(raw_val, byteorder='big', signed=False)
                else:
                    # Fallback : conversion générique
                    int_val = int(raw_val)
            else:
                # uint64 ou autres types numériques : conversion directe
                int_val = int(raw_val)
            if swap_needed:
                int_val = swap_bytes_32(int_val)

        except (ValueError, TypeError) as e:
            if debug:
                print(f"Warning: cannot parse '{raw_val}' (type={type(raw_val).__name__}) as integer, skipping: {e}")
            continue
        
        # Extract each bit field
        for i, field in enumerate(bit_fields_config):
            start = field['start_bit']
            size = field['size']
            signed = field.get('signed', False)
            
            # Extract bits: shift right then mask
            mask = (1 << size) - 1
            extracted = (int_val >> start) & mask
            
            # Handle signed values (two's complement)
            if signed and extracted & (1 << (size - 1)):
                extracted -= (1 << size)
            
            result_dfs[i].loc[timestamp] = float(extracted)
        
        nbItems += 1
        if nbItems == progress_chunk_size:
            monitorProgress.complete_n(nbItems)
            nbItems = 0
    
    monitorProgress.complete_all()
    return result_dfs

def extract_data_any(sourceFolderOrFile, params, targetFile, extractions_config, monitorProgress=None, debug=False):
    """Extract data and print it"""    

    cbPartial = partial(cbExtractBitFields, extractions_config=extractions_config, sourceFolderOrFile=sourceFolderOrFile, debug=debug)
    rst = loadDataframeFromFile(sourceFolderOrFile, params, monitorProgress=monitorProgress, callback=cbPartial)

    written_count = 0
    for dfList in rst:
        for df in dfList:
            h5GroupName = "extract_data_any"
            DataframeToHdf5(targetFile, h5GroupName, df)
            get_logger().info(f"written param {df.name}")
            written_count += 1
        
    get_logger().info(f"written file {str(Path(targetFile).resolve())} with {written_count} params")
    return True

def isInputReadable(f):
    if not os.access(f, os.R_OK):
        get_logger().error("{0} does not exist or is not reachable".format(f))
        sys.exit(1)
    return f

# override the parsing error message using logger
class HelpParser(argparse.ArgumentParser):
    def error(self, message):
        get_logger().error("Input Arguments Error : "+message)
        sys.exit(1)

## the main function
def main():
    
    parser = HelpParser(description=
    """Extract given parameters and decode them using a JSON/YAML config.""",
    formatter_class=argparse.RawTextHelpFormatter)
    
    parser.add_argument('sourceFolderOrFile', metavar="FolderOrFile", 
                        help="source folder or file where to scan data files", type=isInputReadable)
    parser.add_argument('-c', '--config', required=True, help="Path to extraction config (JSON or YAML)")
    parser.add_argument('target', nargs='?', help="Target file to write", default="./extract_data_any.h5")
    parser.add_argument('-p', '--params', metavar='param1,param2,...', 
                        help="Coarse filter for params to load. Regex supported. Ex: 'param_02,my_.*4$'", default=".*")
    parser.add_argument('-d', '--debug', action='store_true', default=False, help="show params contents")
    
    args = parser.parse_args()

    # Charger la configuration externe
    extractions_config = load_extraction_config(args.config)
    
    monitorProgress = MonitorProgress(name="extract_data_any", progressCb=consoleRichProgressCb, debug=False)
    rst = extract_data_any(args.sourceFolderOrFile, args.params, args.target, 
                           extractions_config, monitorProgress=monitorProgress, debug=args.debug)

    if rst == True:
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
