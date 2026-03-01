from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("smolclaw")
except PackageNotFoundError:
    __version__ = "dev"
