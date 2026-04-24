---
name: dagger-python-sdk
description: Use when writing Dagger pipelines in Python, building containerized CI/CD with the Dagger Python SDK, creating Dagger modules/functions/objects, integrating services (PostgreSQL, Redis) in Dagger, using Dagger LLM/agent features, or running Dagger in GitHub Actions.
license: Proprietary
compatibility: Requires Python 3.10+, dagger-io pip package, and a running Dagger Engine (dagger CLI installed). For LLM features requires dagger v0.15+ with LLM support.
metadata:
  author: elviskahoro
  version: "1.0"
  tags: [dagger, python, ci-cd, containers, pipelines, llm, agents]
---

# Dagger Python SDK

Dagger lets you build CI/CD pipelines as code using a fluent Python API. Everything runs in containers — builds, tests, services — composed via a DAG of operations that cache automatically.

## When to Use

- Writing a new Dagger pipeline or module in Python
- Creating containerized build/test/publish workflows
- Defining Dagger functions and object types with `@function` / `@object_type`
- Setting up service dependencies (PostgreSQL, Redis) for tests
- Using cache volumes to speed up pip/npm/apt installs
- Integrating Dagger with GitHub Actions
- Building multi-container pipelines with service bindings
- Using Dagger's LLM/agent features (environments, workspaces, prompts)
- Importing/exporting container images as tarballs
- Running parallel pipeline stages with `anyio`

## Execution Steps for Agents

### Step 1: Choose the Right Connection Pattern

There are two connection patterns. Use the **modern pattern** for new code:

```python
# Modern pattern (preferred) — uses `dag` global
import dagger
from dagger import dag

async with dagger.connection(config=dagger.Config(log_output=sys.stderr)):
    container = dag.container().from_("python:3.11")
    output = await container.with_exec(["python", "-V"]).stdout()
```

```python
# Legacy pattern — uses `client` instance
async with dagger.Connection(dagger.Config(log_output=sys.stderr)) as client:
    container = client.container().from_("python:3.11")
    output = await container.with_exec(["python", "-V"]).stdout()
```

For **Dagger modules** (files loaded by `dagger call`), no connection setup is needed — just use `dag` directly in `@function` decorated methods.

### Step 2: Define Modules with Object Types and Functions

Dagger modules use `@object_type` classes with `@function` methods:

```python
from typing import Annotated
from dagger import dag, function, object_type, Directory, DefaultPath, Doc

@object_type
class MyModule:
    source: Annotated[Directory, DefaultPath(".")]

    @function
    def build(self) -> dagger.Container:
        return (
            dag.container()
            .from_("python:3.11")
            .with_directory("/app", self.source)
            .with_workdir("/app")
            .with_exec(["pip", "install", "-r", "requirements.txt"])
        )

    @function
    async def test(self) -> str:
        return await self.build().with_exec(["pytest"]).stdout()

    @function
    async def publish(self) -> str:
        await self.test()
        return await self.build().publish("ttl.sh/my-app-12345")
```

Key annotations:
- `DefaultPath(".")` — default directory source
- `Doc("description")` — parameter documentation
- `Annotated[Secret, Doc("GitHub token")]` — secret parameters
- `ReturnType.ANY` — allow non-zero exit codes in `with_exec`

### Step 3: Build Container Pipelines

Chain container operations fluently:

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

See [Container API Reference](references/CONTAINER_API.md) for the full method list.

### Step 4: Add Services and Service Bindings

Create service containers and bind them to your pipeline:

```python
@function
async def test(self) -> str:
    postgresdb = (
        dag.container()
        .from_("postgres:alpine")
        .with_env_variable("POSTGRES_DB", "app_test")
        .with_env_variable("POSTGRES_PASSWORD", "secret")
        .with_exposed_port(5432)
        .as_service(args=[], use_entrypoint=True)
    )
    return await (
        self.env()
        .with_service_binding("db", postgresdb)
        .with_env_variable("DATABASE_URL", "postgresql://postgres:secret@db/app_test")
        .with_exec(["pytest", "--tb=short"])
        .stdout()
    )
```

See [Services and Caching Reference](references/SERVICES_AND_CACHING.md) for more patterns.

### Step 5: Use Cache Volumes

Named cache volumes persist across pipeline runs:

```python
apt_cache = dag.cache_volume("apt-cache")
pip_cache = dag.cache_volume("python-pip")
node_cache = dag.cache_volume("node")

container = (
    dag.container()
    .from_("python:3.11")
    .with_mounted_cache("/var/cache/apt/archives", apt_cache)
    .with_mounted_cache("/root/.cache/pip", pip_cache)
)
```

### Step 6: Handle Errors and Exit Codes

Use `ReturnType.ANY` to handle non-zero exit codes gracefully:

```python
from dagger import ReturnType

cmd = (
    self.env()
    .with_exec(["sh", "-c", "pytest --tb=short"], expect=ReturnType.ANY)
)
if await cmd.exit_code() != 0:
    stderr = await cmd.stderr()
    stdout = await cmd.stdout()
    raise Exception(f"Tests failed.\nError: {stderr}\nOutput: {stdout}")
return await cmd.stdout()
```

### Step 7: Run Stages in Parallel

Use `anyio` task groups for concurrent execution:

```python
import anyio

async def test_version(version: str):
    await (
        dag.container()
        .from_(f"python:{version}-slim-buster")
        .with_directory("/src", src)
        .with_workdir("/src")
        .with_exec(["pip", "install", "-r", "requirements.txt"])
        .with_exec(["pytest", "tests"])
        .sync()
    )

async with anyio.create_task_group() as tg:
    for version in ["3.9", "3.10", "3.11", "3.12"]:
        tg.start_soon(test_version, version)
```

### Step 8: Use LLM and Agent Features

Dagger supports LLM-powered environments for AI-driven workflows:

```python
environment = (
    dag.env(privileged=True)
    .with_workspace_input("before", dag.workspace(source=source), "code and tests")
    .with_workspace_output("after", "modified code")
    .with_string_output("summary", "changes made")
)

work = dag.llm().with_env(environment).with_prompt("Fix the failing tests")

summary = await work.env().output("summary").as_string()
result_dir = work.env().output("after").as_workspace().source()
```

See [LLM and Agents Reference](references/LLM_AND_AGENTS.md) for full details.

### Step 9: Integrate with GitHub Actions

```yaml
- name: Dagger - GitHub Action
  uses: dagger/dagger-for-github@8.0.0
  with:
    version: "latest"
    cloud-token: ${{ secrets.DAGGER_CLOUD_TOKEN }}

- name: Install Dagger Python SDK
  run: pip install dagger-io

- name: Run pipeline
  run: dagger run python my_pipeline.py
```

See [Pipelines and CI Reference](references/PIPELINES_AND_CI.md) for workflow patterns.

### Step 10: Import and Export Container Images

```python
# Export to tarball
file_path = await container.export("/path/to/image.tar")

# Import from tarball
imported = dag.container().import_(
    source=dag.host().file(path="/path/to/image.tar", no_cache=True)
)
```

## Quick Reference

| Import | Purpose |
|--------|---------|
| `dag` | Main DAG reference for creating containers, services, etc. |
| `function` | Decorator for Dagger-callable functions |
| `object_type` | Decorator for Dagger module classes |
| `Container` | Container type |
| `Directory` | Directory type |
| `File` | File type |
| `Secret` | Secret type for sensitive data |
| `Service` | Service type |
| `DefaultPath` | Default directory path annotation |
| `Doc` | Documentation annotation |
| `ReturnType` | Exit code handling (`.ANY` allows non-zero) |
| `field` | Field declaration for object types |

| Pattern | Example |
|---------|---------|
| Base image | `dag.container().from_("python:3.11")` |
| Mount source | `.with_directory("/app", source)` |
| Set workdir | `.with_workdir("/app")` |
| Run command | `.with_exec(["pytest"])` |
| Get output | `await container.stdout()` |
| Cache volume | `dag.cache_volume("pip-cache")` |
| Mount cache | `.with_mounted_cache("/root/.cache/pip", cache)` |
| Create service | `.as_service(args=[], use_entrypoint=True)` |
| Bind service | `.with_service_binding("db", service)` |
| Publish image | `await container.publish("ttl.sh/my-app")` |
| Env variable | `.with_env_variable("KEY", "value")` |
| Expose port | `.with_exposed_port(8000)` |
| Create file | `.with_new_file("/path", "contents")` |
| Get file | `container.file("/path")` |
| Get directory | `container.directory("/path")` |
| Exclude dir | `source.without_directory(".dagger")` |
| SSH git clone | `dag.git(url, ssh_auth_socket=sock).ref(ref).tree()` |

## Common Mistakes to Avoid

| Mistake | Problem | Fix |
|---------|---------|-----|
| Using `client` in module code | Modules use `dag`, not a client instance | Use `dag.container()` not `client.container()` |
| Forgetting `await` on `.stdout()` | Returns a coroutine, not the string | Always `await` terminal operations |
| No `expect=ReturnType.ANY` on fallible commands | Pipeline aborts on non-zero exit | Add `expect=ReturnType.ANY` and check `.exit_code()` |
| Missing `.as_service()` on service containers | Container isn't treated as a long-running service | Call `.as_service(args=[], use_entrypoint=True)` |
| Forgetting `.with_exposed_port()` on services | Service binding can't connect | Expose the port before `.as_service()` |
| Not using cache volumes | Slow builds — reinstalls packages every run | Add `dag.cache_volume()` + `.with_mounted_cache()` |
| Using `from_` without underscore | `from` is a Python keyword | Always use `from_("image")` with trailing underscore |
| Using `import` instead of `import_` | `import` is a Python keyword | Always use `import_(source=...)` with trailing underscore |
| Mounting `.dagger` dir in container | Pollutes container with Dagger metadata | Use `source.without_directory(".dagger")` |
| Hardcoding `CACHEBUSTER` | Stale cached results | Use `str(datetime.now())` for cache-busting env vars |

## References

- [Container API Reference](references/CONTAINER_API.md) — Full container method reference
- [Services and Caching Reference](references/SERVICES_AND_CACHING.md) — Services, bindings, cache volumes
- [LLM and Agents Reference](references/LLM_AND_AGENTS.md) — LLM environments, workspaces, multi-agent
- [Modules and Objects Reference](references/MODULES_AND_OBJECTS.md) — Decorators, annotations, custom types
- [Pipelines and CI Reference](references/PIPELINES_AND_CI.md) — GitHub Actions, tracing, real-world patterns
- [Complete Examples](references/EXAMPLES.md) — End-to-end pipeline examples
