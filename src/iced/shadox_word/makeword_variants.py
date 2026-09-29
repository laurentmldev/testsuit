import argparse

from iced.shadox.v2 import ShadoxClient
from iced.shadox_word import MakeWord
from iced.shadox_word.makeword import MakeWordException

import sys
if sys.version_info.major >= 3:
    from pathlib import Path
else:
    from pathlib2 import Path


class MakeWordVariants:
    def __init__(
        self,
        api_url,
        api_key,
        api_key_file,
        api_key_folder,
        project_key,
        dataset_path
    ):
        init_sdx = ShadoxClient(
            api_url,
            api_key,
            project_key=project_key,
            dataset_path=dataset_path,
            api_keys_file_path=api_key_file,
            api_keys_folder_path=api_key_folder
        )

        # Generate one client for each variant of the dataset
        self.clients = {}
        for variant_name in init_sdx.get_variants():
            self.clients[variant_name] = ShadoxClient(
                api_url,
                api_key,
                project_key=project_key,
                dataset_path=dataset_path,
                selector_type="variant",
                selector_value=variant_name
            )

    def make(self, input_files, output_dir):
        # Create the output directory
        output_path = Path(output_dir)
        output_path.mkdir(exist_ok=True)

        mw = MakeWord(self.clients)
        for fp in input_files:
            out_doc = output_path / fp
            try:
                mw.make(fp, out_doc)
            except MakeWordException as e:
                print("Error while processing file " + fp + " (reason: " + str(e) + ")")
                break
        else:
            print("Output documents are available at " + str(output_path))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('api_url', type=str)
    parser.add_argument('project_key', type=str)
    parser.add_argument('dataset_path', type=str)
    parser.add_argument('-k', '--api_key', type=str, default=None, metavar='<api_key>')
    parser.add_argument('-K', '--api_key_file', type=str, default=None, metavar='<api_key_path>')
    parser.add_argument('-F', '--api_key_folder', type=str, default=None, metavar='<api_key_folder>')
    parser.add_argument('-o', type=str, default='out', help='Set the output directory', metavar='<output_dir>')
    parser.add_argument('-i', nargs='+', required=True, type=str, help='[REQUIRED] Set the input template files', metavar='<input_file>')

    args = parser.parse_args()

    mkv = MakeWordVariants(
        args.api_url,
        args.api_key,
        args.api_key_file,
        args.api_key_folder,
        args.project_key,
        args.dataset_path,
    )
    mkv.make(args.i, args.o)
