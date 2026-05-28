import os
import sys
import tempfile
import unittest
from unittest import mock


REPO_ROOT = os.path.dirname(os.path.dirname(__file__))
SRC_ROOT = os.path.join(REPO_ROOT, "src")
if SRC_ROOT not in sys.path:
    sys.path.insert(0, SRC_ROOT)

from tonemuso import emulation


class LoadLibsDictTests(unittest.TestCase):
    EMPTY_CELL_BOC_B64 = "te6ccuEBAQEAAgAEAABmLc6k"

    def test_loads_binary_boc_file_as_base64_cell_string(self) -> None:
        raw_boc = bytes.fromhex("b5ee9c720101010100020000")

        with tempfile.NamedTemporaryFile() as f:
            f.write(raw_boc)
            f.flush()

            with mock.patch.dict(os.environ, {"EMULATOR_LIBS_BOC_PATH": f.name}), mock.patch(
                "tonemuso.emulation.Cell", side_effect=lambda value: ("cell", value)
            ) as cell_mock, mock.patch("tonemuso.emulation.VmDict", return_value="vmdict") as dict_mock:
                result = emulation.load_libs_dict({"libs": "from-block"})

        self.assertEqual(result, "vmdict")
        cell_mock.assert_called_once_with("te6ccgEBAQEAAgAA")
        dict_mock.assert_called_once_with(256, False, cell_root=("cell", "te6ccgEBAQEAAgAA"))

    def test_loads_ascii_hex_dump_file_as_base64_cell_string(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w") as f:
            f.write("b5ee9c720101010100020000")
            f.flush()

            with mock.patch.dict(os.environ, {"EMULATOR_LIBS_BOC_PATH": f.name}), mock.patch(
                "tonemuso.emulation.Cell", side_effect=[Exception("not boc string"), ("cell", "converted")]
            ) as cell_mock, mock.patch("tonemuso.emulation.VmDict", return_value="vmdict"):
                result = emulation.load_libs_dict({"libs": "from-block"})

        self.assertEqual(result, "vmdict")
        self.assertEqual(
            cell_mock.call_args_list,
            [mock.call("b5ee9c720101010100020000"), mock.call("te6ccgEBAQEAAgAA")],
        )

    def test_binary_boc_file_is_accepted_by_real_cell_parser(self) -> None:
        raw_boc = emulation.base64.b64decode(self.EMPTY_CELL_BOC_B64)

        with tempfile.NamedTemporaryFile() as f:
            f.write(raw_boc)
            f.flush()

            with mock.patch.dict(os.environ, {"EMULATOR_LIBS_BOC_PATH": f.name}), mock.patch(
                "tonemuso.emulation.VmDict", side_effect=lambda *args, **kwargs: kwargs["cell_root"]
            ):
                cell = emulation.load_libs_dict({"libs": "from-block"})

        self.assertEqual(cell.to_boc(), self.EMPTY_CELL_BOC_B64)


if __name__ == "__main__":
    unittest.main()
