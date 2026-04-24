# Modules and Objects Reference

## Dagger Module Structure

A Dagger module is a Python package that defines functions and object types that can be called via `dagger call`.

### Imports

```python
from typing import Annotated, Self
from datetime import datetime
import random

import dagger
from dagger import (
    Container,
    dag,
    Directory,
    DefaultPath,
    Doc,
    File,
    Secret,
    Service,
    Socket,
    function,
    object_type,
    ReturnType,
    field,
)
```

## Decorators

### `@object_type`

Marks a class as a Dagger object type (module entry point):

```python
@object_type
class MyModule:
    source: Annotated[dagger.Directory, DefaultPath(".")]

    @function
    async def build(self) -> str:
        ...
```

### `@function`

Marks a method as callable via `dagger call`:

```python
@function
async def test(self) -> str:
    return await self.env().with_exec(["pytest"]).stdout()

@function
def build(self) -> dagger.Container:
    return dag.container().from_("python:3.11")
```

Functions can be:
- **Sync** — returns a Dagger type (Container, Directory, etc.) for further chaining
- **Async** — returns a resolved value (str, int, list) after `await`

## Annotations

### `DefaultPath`

Sets a default directory for `Directory` parameters:

```python
source: Annotated[dagger.Directory, DefaultPath(".")]
# or
source: Annotated[dagger.Directory, DefaultPath("/")]
```

- `DefaultPath(".")` — current module directory
- `DefaultPath("/")` — root context directory

### `Doc`

Documents a parameter for the Dagger API:

```python
@function
async def fix(
    self,
    source: Annotated[dagger.Directory, DefaultPath("/")],
    repository: Annotated[str, Doc("The owner and repository name")],
    ref: Annotated[str, Doc("The git ref")],
    token: Annotated[Secret, Doc("GitHub API token")],
) -> str:
    ...
```

### Combined Annotations

```python
source: Annotated[
    dagger.Directory,
    DefaultPath("/"),
    Doc("The source directory to build"),
]
```

## Type System

### Core Types

| Type | Description | Terminal methods |
|------|-------------|-----------------|
| `Container` | A container with filesystem and execution | `.stdout()`, `.stderr()`, `.exit_code()`, `.sync()` |
| `Directory` | A directory of files | `.entries()`, `.file()`, `.glob()` |
| `File` | A single file | `.contents()`, `.export()` |
| `Secret` | A sensitive value | Used in env vars, not directly readable |
| `Service` | A long-running container | Bound via `.with_service_binding()` |
| `Socket` | A Unix socket | Used for SSH auth |
| `CacheVolume` | A named persistent cache | Mounted via `.with_mounted_cache()` |

### Return Types

```python
@function
def build(self) -> dagger.Container:      # Returns chainable container
    ...

@function
async def test(self) -> str:              # Returns resolved string
    ...

@function
def run(self) -> dagger.Service:          # Returns a service
    ...

@function
async def publish(self) -> str:           # Returns image reference
    ...
```

### Optional Parameters

```python
@function
async def fix(
    self,
    repository: Annotated[str, Doc("Owner/repo")] | None = None,
    ref: Annotated[str, Doc("Git ref")] | None = None,
    token: Annotated[Secret, Doc("GitHub token")] | None = None,
    fix: Annotated[bool, Doc("Whether to open a PR")] = True,
) -> str:
    if repository and ref and token:
        return await self.fix_github(...)
    else:
        return await self.fix_local(...)
```

## Custom Return Types

Use `@object_type` with `field()` for structured returns:

```python
@object_type
class Result:
    fdirectory: dagger.Directory = field()
    fsummary: str = field()

@object_type
class MyModule:
    @function
    async def fix(self, source: dagger.Directory) -> Result:
        directory = await self.fix_local(source)
        return Result(fdirectory=directory, fsummary="Fix completed")
```

## Self-Referencing Methods

Use `Self` type for methods that return the modified object:

```python
from typing import Self

@object_type
class Workspace:
    ctr: Container
    source: Directory

    @function
    def write_file(self, path: str, contents: str) -> Self:
        self.ctr = self.ctr.with_new_file(path, contents)
        return self
```

## Class Methods for Construction

```python
@object_type
class Workspace:
    ctr: Container
    source: Directory

    @classmethod
    async def create(
        cls,
        source: Annotated[Directory, Doc("Source"), DefaultPath("/")],
    ):
        ctr = (
            dag.container()
            .from_("python:3.11")
            .with_workdir("/app")
            .with_directory("/app", source)
            .with_exec(["pip", "install", "-r", "requirements.txt"])
        )
        return cls(ctr=ctr, source=source)
```

## Method Composition

Structure modules with reusable base methods:

```python
@object_type
class Book:
    source: Annotated[dagger.Directory, DefaultPath(".")]

    @function
    def env(self, version: str = "3.11") -> dagger.Container:
        """Base environment — reused by test, build, serve"""
        return (
            dag.container()
            .from_(f"python:{version}")
            .with_directory("/app", self.source.without_directory(".dagger"))
            .with_workdir("/app")
            .with_mounted_cache("/root/.cache/pip", dag.cache_volume("python-pip"))
            .with_exec(["pip", "install", "-r", "requirements.txt"])
        )

    @function
    def build(self) -> dagger.Container:
        """Builds on env()"""
        return self.env().with_exposed_port(8000).with_entrypoint([...])

    @function
    async def test(self) -> str:
        """Uses env() with test services"""
        return await self.env().with_service_binding(...).with_exec([...]).stdout()

    @function
    async def publish(self) -> str:
        """Runs test() then publishes build()"""
        await self.test()
        return await self.build().publish("ttl.sh/my-app")

    @function
    def serve(self) -> dagger.Service:
        """Serves build() with database"""
        return self.build().with_service_binding(...).as_service(...)
```
