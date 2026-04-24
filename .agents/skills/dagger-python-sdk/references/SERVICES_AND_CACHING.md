# Services and Caching Reference

## Services

Services are long-running containers (databases, web servers, etc.) that other containers connect to via service bindings.

### Creating a Service

```python
postgresdb = (
    dag.container()
    .from_("postgres:alpine")
    .with_env_variable("POSTGRES_DB", "app")
    .with_env_variable("POSTGRES_PASSWORD", "secret")
    .with_exposed_port(5432)
    .as_service(args=[], use_entrypoint=True)
)
```

**Required steps:**
1. Set the base image with `.from_()`
2. Configure with env vars
3. Expose the port with `.with_exposed_port()`
4. Convert to service with `.as_service(args=[], use_entrypoint=True)`

### Binding a Service

```python
container = (
    self.env()
    .with_service_binding("db", postgresdb)
    .with_env_variable("DATABASE_URL", "postgresql://postgres:secret@db/app")
)
```

The first argument to `.with_service_binding()` is the **hostname** the service is reachable at. In the example above, `"db"` means the service is accessible at `db:5432`.

### Service Patterns

#### PostgreSQL for Testing

```python
@function
async def test(self) -> str:
    postgresdb = (
        dag.container()
        .from_("postgres:alpine")
        .with_env_variable("POSTGRES_DB", "app_test")
        .with_env_variable("POSTGRES_PASSWORD", "app_test_secret")
        .with_exposed_port(5432)
        .as_service(args=[], use_entrypoint=True)
    )
    cmd = (
        self.env()
        .with_service_binding("db", postgresdb)
        .with_env_variable("DATABASE_URL", "postgresql://postgres:app_test_secret@db/app_test")
        .with_env_variable("CACHEBUSTER", str(datetime.now()))
        .with_exec(["sh", "-c", "PYTHONPATH=$(pwd) pytest --tb=short"], expect=ReturnType.ANY)
    )
    if await cmd.exit_code() != 0:
        stderr = await cmd.stderr()
        stdout = await cmd.stdout()
        raise Exception(f"Tests failed.\nError: {stderr}\nOutput: {stdout}")
    return await cmd.stdout()
```

#### FastAPI with PostgreSQL (Serve)

```python
@function
def serve(self) -> dagger.Service:
    postgresdb = (
        dag.container()
        .from_("postgres:alpine")
        .with_env_variable("POSTGRES_DB", "app")
        .with_env_variable("POSTGRES_PASSWORD", "app_secret")
        .with_exposed_port(5432)
        .as_service(args=[], use_entrypoint=True)
    )
    return (
        self.build()
        .with_service_binding("db", postgresdb)
        .with_env_variable("DATABASE_URL", "postgresql://postgres:app_secret@db/app")
        .as_service(args=[], use_entrypoint=True)
    )
```

#### Returning a Service from a Function

```python
@function
def run(self) -> dagger.Service:
    return (
        self.env()
        .with_service_binding("db", postgresdb)
        .with_env_variable("DATABASE_URL", "postgresql://postgres:secret@db/app")
        .with_exec(["fastapi", "run", "main.py"])
        .as_service()
    )
```

## Cache Volumes

Cache volumes persist data across pipeline runs, speeding up repeated operations.

### Creating and Mounting

```python
pip_cache = dag.cache_volume("python-pip")
apt_cache = dag.cache_volume("apt-cache")
node_cache = dag.cache_volume("node")

container = (
    dag.container()
    .from_("python:3.11")
    .with_mounted_cache("/root/.cache/pip", pip_cache)
    .with_mounted_cache("/var/cache/apt/archives", apt_cache)
)
```

### Common Cache Paths

| Package Manager | Cache Volume Name | Mount Path |
|-----------------|-------------------|------------|
| pip | `"python-pip"` | `/root/.cache/pip` |
| npm | `"node"` | `/root/.npm` |
| apt | `"apt-cache"` | `/var/cache/apt/archives` |
| maven | `"maven"` | `/root/.m2` |
| go | `"go-mod"` | `/go/pkg/mod` |

### Cache-Busting with Environment Variables

Use a timestamp env var to force re-execution when needed:

```python
from datetime import datetime

container = (
    self.env()
    .with_env_variable("CACHEBUSTER", str(datetime.now()))
    .with_exec(["pytest"])
)
```

### Full Example: Cached Python Environment

```python
@function
def env(self, version: str = "3.11") -> dagger.Container:
    return (
        dag.container()
        .from_(f"python:{version}")
        .with_directory("/app", self.source.without_directory(".dagger"))
        .with_workdir("/app")
        .with_mounted_cache("/root/.cache/pip", dag.cache_volume("python-pip"))
        .with_exec(["pip", "install", "-r", "requirements.txt"])
    )
```
