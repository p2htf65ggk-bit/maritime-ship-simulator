# SPDX-License-Identifier: MPL-2.0
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_delvewheel_libs_directory, collect_submodules, copy_metadata


# GDAL's S57 object/attribute dictionaries must also be available without a
# system GDAL install. On Windows, delvewheel bundles DLLs next to the package.
datas = collect_data_files("pyogrio") + copy_metadata("pyogrio")
binaries = collect_dynamic_libs("pyogrio")
datas, binaries = collect_delvewheel_libs_directory("pyogrio", datas=datas, binaries=binaries)
hiddenimports = collect_submodules("pyogrio") + ["uuid"]  # Imports inside compiled Cython extensions.
