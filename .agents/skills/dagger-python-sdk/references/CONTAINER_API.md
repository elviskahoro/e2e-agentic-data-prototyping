# Container API Reference

Complete reference for `dagger.Container` methods in the Python SDK.

## Creating Containers

| Method | Signature | Description |
|--------|-----------|-------------|
| `dag.container()` | `-> Container` | Create an empty container |
| `.from_(address)` | `(str) -> Container` | Set base image (note trailing underscore) |
| `.import_(source)` | `(File) -> Container` | Import container from tarball file |

## File and Directory Operations

| Method | Signature | Description |
|--------|-----------|-------------|
| `.with_directory(path, dir)` | `(str, Directory) -> Container` | Mount a directory at path |
| `.with_mounted_directory(path, source)` | `(str, Directory) -> Container` | Mount directory (alias) |
| `.with_mounted_cache(path, cache)` | `(str, CacheVolume) -> Container` | Mount a named cache volume |
| `.with_new_file(path, contents)` | `(str, str) -> Container` | Create a file with contents |
| `.with_file(path, source)` | `(str, File) -> Container` | Copy a file into the container |
| `.without_directory(path)` | `(str) -> Container` | Remove a directory |
| `.directory(path)` | `(str) -> Directory` | Get a directory from container |
| `.file(path)` | `(str) -> File` | Get a file from container |

## Execution

| Method | Signature | Description |
|--------|-----------|-------------|
| `.with_exec(args)` | `(list[str], expect=ReturnType) -> Container` | Execute a command |
| `.with_exec(args, expect=ReturnType.ANY)` | `(list[str]) -> Container` | Execute allowing non-zero exit |
| `.with_exec(args, redirect_stdout=path)` | `(list[str], str) -> Container` | Execute and redirect stdout to file |
| `.stdout()` | `-> str` | Get stdout (async) |
| `.stderr()` | `-> str` | Get stderr (async) |
| `.exit_code()` | `-> int` | Get exit code (async) |
| `.sync()` | `-> None` | Execute and wait (async) |

## Configuration

| Method | Signature | Description |
|--------|-----------|-------------|
| `.with_workdir(path)` | `(str) -> Container` | Set working directory |
| `.with_entrypoint(args)` | `(list[str]) -> Container` | Set container entrypoint |
| `.with_env_variable(name, value)` | `(str, str) -> Container` | Set environment variable |
| `.with_exposed_port(port)` | `(int) -> Container` | Expose a port |

## Publishing and Export

| Method | Signature | Description |
|--------|-----------|-------------|
| `.publish(address)` | `(str) -> str` | Publish to registry, returns image ref (async) |
| `.export(path)` | `(str) -> str` | Export to local tarball, returns path (async) |
| `.as_service(args, use_entrypoint)` | `(list[str], bool) -> Service` | Convert to a long-running service |

## Directory Methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `.entries()` | `-> list[str]` | List directory entries (async) |
| `.file(path)` | `(str) -> File` | Get a file from directory |
| `.glob(pattern)` | `(str) -> list[str]` | Glob match files (async) |
| `.without_directory(path)` | `(str) -> Directory` | Remove a subdirectory |
| `.with_new_file(path, contents)` | `(str, str) -> Directory` | Add a file to directory |

## File Methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `.contents()` | `-> str` | Get file contents (async) |
| `.export(path)` | `(str) -> str` | Export file to host (async) |

## Host Methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `dag.host().directory(path)` | `(str) -> Directory` | Get host directory |
| `dag.host().directory(path, exclude=[...])` | `(str, list[str]) -> Directory` | Get host directory with exclusions |
| `dag.host().file(path)` | `(str) -> File` | Get host file |
| `dag.host().file(path, no_cache=True)` | `(str) -> File` | Get host file without caching |

## Git Operations

| Method | Signature | Description |
|--------|-----------|-------------|
| `dag.git(url)` | `(str) -> GitRepository` | Clone a git repository |
| `dag.git(url, ssh_auth_socket=sock)` | `(str, Socket) -> GitRepository` | Clone with SSH auth |
| `.ref(name)` | `(str) -> GitRef` | Get a git reference |
| `.tree()` | `-> Directory` | Get the file tree |

## Method Chaining Example

```python
container = (
    dag.container()
    .from_("python:3.11")
    .with_directory("/app", source.without_directory(".dagger"))
    .with_workdir("/app")
    .with_mounted_cache("/root/.cache/pip", dag.cache_volume("python-pip"))
    .with_exec(["pip", "install", "-r", "requirements.txt"])
    .with_env_variable("PYTHONPATH", "/app")
    .with_exposed_port(8000)
    .with_entrypoint(["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"])
)
```
