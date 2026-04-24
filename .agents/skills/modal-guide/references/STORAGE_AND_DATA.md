# Storage and Data Reference

Complete reference for persistent storage, data management, and caching on Modal.

## Storage Options

| Type | Use Case | Persistence | Access Pattern |
|------|----------|-------------|----------------|
| `modal.Volume` | Model weights, checkpoints, datasets | Persistent | Read/write filesystem |
| `modal.CloudBucketMount` | S3/GCS data | External | Read/write filesystem |
| `modal.NetworkFileSystem` | Shared NFS | Persistent | Multi-container read/write |
| Local files in image | Config, small static files | Immutable (per deploy) | Built into container |

## Volumes

### Create and Mount

```python
vol = modal.Volume.from_name("my-volume", create_if_missing=True)

@app.function(volumes={"/data": vol})
def process():
    # Read
    data = Path("/data/input.txt").read_text()

    # Write
    Path("/data/output.txt").write_text("result")

    # IMPORTANT: commit to persist writes
    vol.commit()
```

### Volume API

```python
vol = modal.Volume.from_name("my-volume")

# Programmatic access (outside a function)
vol.put_file("local_file.txt", "/remote/path.txt")
vol.get_file("/remote/path.txt", "local_output.txt")
vol.listdir("/some/path")

# Inside a function — use filesystem directly
@app.function(volumes={"/data": vol})
def fn():
    os.listdir("/data")
    vol.reload()   # Refresh to see changes from other containers
    vol.commit()   # Persist changes
```

### Volume Best Practices

- Always call `vol.commit()` after writing
- Call `vol.reload()` before reading if another container may have written
- Use for: model weights, training checkpoints, cached downloads, datasets
- Don't use for: temporary scratch files (just use local disk)

## Cloud Bucket Mounts (S3/GCS)

```python
s3_secret = modal.Secret.from_name(
    "s3-credentials",
    required_keys=["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"],
)

@app.function(
    volumes={
        "/bucket": modal.CloudBucketMount(
            "my-s3-bucket",
            secret=s3_secret,
            read_only=False,
        )
    }
)
def process_s3():
    # Read/write like a local filesystem
    files = list(Path("/bucket/data").glob("*.parquet"))

    # DuckDB can query directly
    import duckdb
    df = duckdb.sql(f"SELECT * FROM read_parquet('{files[0]}')").df()
```

## Secrets

### Creating Secrets

```python
# Reference a named secret (created in Modal dashboard or CLI)
secret = modal.Secret.from_name("my-secret")

# With required keys (fails fast if missing)
secret = modal.Secret.from_name(
    "my-secret",
    required_keys=["API_KEY", "API_SECRET"],
)
```

### Using Secrets

```python
@app.function(secrets=[secret])
def use_credentials():
    import os
    key = os.environ["API_KEY"]
    # Secrets are injected as environment variables
```

### Common Secret Patterns

| Service | Secret Name Convention | Keys |
|---------|----------------------|------|
| Hugging Face | `huggingface-secret` | `HF_TOKEN` |
| Weights & Biases | `wandb-secret` | `WANDB_API_KEY` |
| AWS/S3 | `s3-credentials` | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` |
| Postgres | `postgres-secret` | `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD` |
| OpenAI | `openai-secret` | `OPENAI_API_KEY` |

## Caching Patterns

### HuggingFace Model Cache

```python
hf_cache = modal.Volume.from_name("hf-cache", create_if_missing=True)

@app.cls(
    gpu="H100",
    volumes={"/root/.cache/huggingface": hf_cache},
    secrets=[modal.Secret.from_name("huggingface-secret")],
)
class Server:
    @modal.enter()
    def load(self):
        # First run: downloads and caches to volume
        # Subsequent runs: loads from volume (fast)
        self.model = AutoModel.from_pretrained("meta-llama/Llama-3-8B")
```

### Download Once Pattern

```python
def download_model():
    """Run during image build to bake model into the image."""
    from huggingface_hub import snapshot_download
    snapshot_download("meta-llama/Llama-3-8B", local_dir="/models/llama")

image = (
    modal.Image.debian_slim()
    .pip_install("huggingface_hub[hf_transfer]")
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1"})
    .run_function(download_model, secrets=[hf_secret])
)
```

## Large Dataset Ingestion

```python
@app.function(
    volumes={
        "/bucket": modal.CloudBucketMount("dataset-bucket", secret=s3_secret)
    },
    timeout=3600,
)
def ingest_dataset(urls: list[str]):
    import urllib.request

    for url in urls:
        filename = url.split("/")[-1]
        urllib.request.urlretrieve(url, f"/bucket/data/{filename}")
    # CloudBucketMount auto-syncs — no commit needed
```

## Symlink Pattern (Framework Compatibility)

```python
@app.function(volumes={"/data": data_vol})
def train():
    # Many frameworks expect data in specific locations
    os.symlink("/data/train.bin", "/root/project/data/train.bin")
    os.symlink("/data/val.bin", "/root/project/data/val.bin")
    # Now the training script finds data where it expects
```
