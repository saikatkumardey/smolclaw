from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("smolclaw")
except PackageNotFoundError:
    __version__ = "dev"
