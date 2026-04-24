"""Dagger module for dlt data generation: runs a synthetic dlt pipeline in a container."""

try:
    from .main import DltDatagen as DltDatagen
except ImportError:
    # dagger-io isn't installed (e.g. inside the container that runs the dlt
    # pipeline itself). The package is still importable so consumers can pull
    # `dlt_datagen.load` for the resource definitions.
    pass
