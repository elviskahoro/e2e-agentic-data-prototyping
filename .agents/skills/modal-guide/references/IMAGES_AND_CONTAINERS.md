# Images and Containers Reference

Complete reference for building container images on Modal.

## Base Images

| Method | Description |
|--------|-------------|
| `modal.Image.debian_slim(python_version="3.11")` | Minimal Debian — default, best for most apps |
| `modal.Image.ubuntu(version="22.04")` | Ubuntu base |
| `modal.Image.alpine()` | Minimal Alpine Linux |
| `modal.Image.from_registry("nvidia/cuda:12.4.0-devel-ubuntu22.04")` | Any Docker Hub image |
| `modal.Image.from_dockerfile("./Dockerfile")` | Build from Dockerfile |

**CUDA note:** GPU functions automatically have CUDA drivers. You only need NVIDIA base images if you need the CUDA toolkit (nvcc compiler, cuDNN headers, etc.).

## Image Builder Methods (Chained)

Each method returns a new image layer. Order matters — layers are cached.

```python
image = (
    modal.Image.debian_slim(python_version="3.11")
    # System packages
    .apt_install("libpq-dev", "ffmpeg", "git")

    # Python packages (preferred — uses uv for speed)
    .uv_pip_install("torch~=2.6.0", "transformers~=4.51.0")

    # Alternative pip install
    .pip_install("requests~=2.31.0")

    # Environment variables
    .env({"HF_HOME": "/cache", "TOKENIZERS_PARALLELISM": "false"})

    # Add local files/directories into the image
    .add_local_file("config.yaml", "/root/config.yaml")
    .add_local_dir("./src", remote_path="/root/src")

    # Run arbitrary shell commands
    .run_commands("pip install flash-attn --no-build-isolation")

    # Run a Python function during build
    .run_function(download_model, secrets=[hf_secret])

    # Clear entrypoint (useful with nvidia/cuda images that have default entrypoints)
    .entrypoint([])

    # Dockerfile-style commands
    .dockerfile_commands("RUN apt-get update && apt-get install -y curl")
)
```

## Dependency Pinning Rules

From Modal's official style guide:

| Package Version | Pinning Style | Example |
|----------------|---------------|---------|
| `>= 1.0.0` (SemVer stable) | Compatible release `~=` | `torch~=2.6.0` |
| `< 1.0.0` (pre-stable) | Exact patch `==` | `vllm==0.7.3` |
| Base images | Specific tag, never `latest` | `nvidia/cuda:12.4.0-devel-ubuntu22.04` |
| Python | Explicit version | `python_version="3.11"` |

## Conditional Imports

For packages only available inside the Modal container:

```python
with image.imports():
    import torch
    import transformers
    from vllm import LLM
```

This prevents `ImportError` when the script is parsed locally.

## CUDA / Flash Attention Image Pattern

```python
cuda_version = "12.4.0"
flavor = "devel"  # Need 'devel' for nvcc
os_version = "ubuntu22.04"

image = (
    modal.Image.from_registry(
        f"nvidia/cuda:{cuda_version}-{flavor}-{os_version}",
        add_python="3.11",
    )
    .entrypoint([])  # Suppress NVIDIA default entrypoint
    .apt_install("git")
    .pip_install(
        "torch==2.6.0",
        "flash-attn==2.7.4",  # Pin exact version
    )
)
```

## Image Builder Versions

Modal's image builder has versions that affect available features:

| Version | Status | Notable Changes |
|---------|--------|-----------------|
| `2023.12` | Legacy | Original builder |
| `2024.04` | Stable | Improved caching |
| `2024.10` | Current default | Better layer dedup |
| `2025.06` | Latest | New features |
| `PREVIEW` | Experimental | Cutting edge |

## Multi-Stage Build Pattern

```python
# Stage 1: Build with full toolkit
build_image = (
    modal.Image.from_registry("nvidia/cuda:12.4.0-devel-ubuntu22.04")
    .run_commands("pip install flash-attn --no-build-isolation")
)

# Stage 2: Runtime with minimal image
runtime_image = (
    modal.Image.debian_slim()
    .pip_install("torch~=2.6.0")
    .copy(build_image, "/usr/local/lib/python3.11/", "/usr/local/lib/python3.11/")
)
```

## Key Paths in Container

| Path | Purpose |
|------|---------|
| `/root` | Default working directory |
| `/root/.cache` | Common cache location |
| `/root/.cache/huggingface` | HuggingFace model cache |
| Volume mounts | Configured via `volumes={}` parameter |
