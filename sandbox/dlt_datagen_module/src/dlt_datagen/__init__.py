"""dlt data generation package: defines the datagen_source for dlt pipelines."""

try:
    from .load import datagen_source as datagen_source
except ImportError:
    # In contexts where load.py dependencies aren't available, the package is
    # still importable so consumers can use the module structure.
    pass
