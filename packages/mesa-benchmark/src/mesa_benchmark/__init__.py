from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("mesa-benchmark")
except PackageNotFoundError:
    __version__ = "0.0.0"
