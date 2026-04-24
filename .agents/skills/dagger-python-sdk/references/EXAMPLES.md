# Complete Examples

End-to-end examples demonstrating real-world Dagger Python SDK patterns.

## Example 1: FastAPI Application Module

Full module for a FastAPI app with PostgreSQL, testing, building, serving, and publishing.

```python
import random
from typing import Annotated
from datetime import datetime

import dagger
from dagger import (
    Container, dag, field, Directory, DefaultPath, Doc,
    File, Secret, function, object_type, ReturnType,
)


@object_type
class Result:
    """Custom return type for fix results"""
    fdirectory: dagger.Directory = field()
    fsummary: str = field()


@object_type
class Book:
    source: Annotated[dagger.Directory, DefaultPath(".")]

    @function
    def env(self, version: str = "3.11") -> dagger.Container:
        """Base Python environment with dependencies installed"""
        return (
            dag.container()
            .from_(f"python:{version}")
            .with_directory("/app", self.source.without_directory(".dagger"))
            .with_workdir("/app")
            .with_mounted_cache("/root/.cache/pip", dag.cache_volume("python-pip"))
            .with_exec(["pip", "install", "-r", "requirements.txt"])
        )

    @function
    async def test(self) -> str:
        """Run tests with a PostgreSQL service"""
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
            .with_env_variable(
                "DATABASE_URL",
                "postgresql://postgres:app_test_secret@db/app_test",
            )
            .with_env_variable("CACHEBUSTER", str(datetime.now()))
            .with_exec(
                ["sh", "-c", "PYTHONPATH=$(pwd) pytest --tb=short"],
                expect=ReturnType.ANY,
            )
        )
        if await cmd.exit_code() != 0:
            stderr = await cmd.stderr()
            stdout = await cmd.stdout()
            raise Exception(f"Tests failed.\nError: {stderr}\nOutput: {stdout}")
        return await cmd.stdout()

    @function
    def build(self) -> dagger.Container:
        """Build the application container with entrypoint"""
        return (
            self.env()
            .with_exposed_port(8000)
            .with_entrypoint([
                "uvicorn", "main:app",
                "--host", "0.0.0.0", "--port", "8000",
            ])
        )

    @function
    def serve(self) -> dagger.Service:
        """Serve the app with a PostgreSQL database"""
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
            .with_env_variable(
                "DATABASE_URL",
                "postgresql://postgres:app_secret@db/app",
            )
            .as_service(args=[], use_entrypoint=True)
        )

    @function
    async def publish(self) -> str:
        """Test then publish to a registry"""
        await self.test()
        return await self.build().publish(
            f"ttl.sh/my-fastapi-app-{random.randrange(10**8)}"
        )
```

**Usage:**
```bash
dagger call test
dagger call build
dagger call serve
dagger call publish
```

## Example 2: Workspace with Code Editing Tools

A workspace module that provides file operations and test capabilities for LLM agents.

```python
from typing import Annotated, Self
from datetime import datetime

from dagger import (
    Container, dag, Directory, DefaultPath, Doc,
    function, object_type, ReturnType,
)


@object_type
class Workspace:
    ctr: Container
    source: Directory

    @classmethod
    async def create(
        cls,
        source: Annotated[Directory, Doc("Source code"), DefaultPath("/")],
    ):
        ctr = (
            dag.container()
            .from_("python:3.11")
            .with_workdir("/app")
            .with_directory("/app", source)
            .with_mounted_cache("/root/.cache/pip", dag.cache_volume("python-pip"))
            .with_exec(["pip", "install", "-r", "requirements.txt"])
        )
        return cls(ctr=ctr, source=source)

    @function
    async def read_file(
        self,
        path: Annotated[str, Doc("File path to read")],
    ) -> str:
        return await self.ctr.file(path).contents()

    @function
    def write_file(
        self,
        path: Annotated[str, Doc("File path to write")],
        contents: Annotated[str, Doc("File contents")],
    ) -> Self:
        self.ctr = self.ctr.with_new_file(path, contents)
        return self

    @function
    async def list_files(
        self,
        path: Annotated[str, Doc("Directory path")],
    ) -> list[str]:
        return await self.ctr.directory(path).entries()

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
        cmd = (
            self.ctr
            .with_service_binding("db", postgresdb)
            .with_env_variable(
                "DATABASE_URL",
                "postgresql://postgres:secret@db/app_test",
            )
            .with_env_variable("CACHEBUSTER", str(datetime.now()))
            .with_exec(
                ["sh", "-c", "PYTHONPATH=$(pwd) pytest --tb=short"],
                expect=ReturnType.ANY,
            )
        )
        if await cmd.exit_code() != 0:
            raise Exception(
                f"Tests failed.\n{await cmd.stderr()}\n{await cmd.stdout()}"
            )
        return await cmd.stdout()

    @function
    def container(self) -> Container:
        return self.ctr
```

## Example 3: LLM-Powered Code Fixer with GitHub Integration

```python
@function
async def diagnose(
    self,
    source: Annotated[dagger.Directory, DefaultPath("/")],
    repository: Annotated[str, Doc("The owner and repository name")],
    ref: Annotated[str, Doc("The ref name")],
    token: Annotated[Secret, Doc("GitHub API token")],
    fix: Annotated[bool, Doc("Whether to open a PR")] = True,
) -> str:
    environment = (
        dag.env(privileged=True)
        .with_workspace_input(
            "before",
            dag.workspace(source=source),
            "code and tests workspace",
        )
        .with_workspace_output("after", "modified code")
        .with_string_output("summary", "proposed changes")
    )

    work = dag.llm().with_env(environment).with_prompt("""
        - You are an expert Python developer
        - The tests are failing
        - Fix the issues so tests pass
        - Always write changes to the workspace
        - Run the test tool after changes
        - Summarize your changes as proposed actions
    """)

    summary = await work.env().output("summary").as_string()

    diff_file = await (
        work.env().output("after").as_workspace().container()
        .with_exec(["sh", "-c", "git diff > /tmp/a.diff"])
        .file("/tmp/a.diff")
    )

    if fix:
        pr_url = await dag.github_api().create_pr(
            repository, ref, diff_file, token
        )

    diff = await diff_file.contents()
    comment_body = f"{summary}\n\nDiff:\n\n```{diff}```"
    if fix:
        comment_body += f"\n\nPR with fixes: {pr_url}"

    comment_url = await dag.github_api().create_comment(
        repository, ref, comment_body, token
    )
    return f"Comment posted: {comment_url}"
```

## Example 4: Standalone Pipeline Script with Docker Image Caching

```python
import sys
import asyncio
import os
import dagger
from dagger import Container, dag

DEFAULT_IMAGE = "python:3.11-slim-buster"
IMAGE_CACHE = ".dagger/docker-images/python-3.11.tar"


async def run_pipeline(use_cache: bool = True):
    async with dagger.connection(
        config=dagger.Config(log_output=sys.stderr, timeout=3)
    ):
        # Load or pull container
        if use_cache and os.path.exists(IMAGE_CACHE):
            ctr = dag.container().import_(
                source=dag.host().file(path=IMAGE_CACHE, no_cache=True)
            )
        else:
            ctr = (
                dag.container()
                .from_(DEFAULT_IMAGE)
                .with_exec(["pip", "install", "--upgrade", "pip"])
                .with_exec(["pip", "install", "uv"])
            )
            # Save for next time
            await ctr.export(IMAGE_CACHE)

        # Mount source and install deps
        source = dag.host().directory("./src")
        pipeline = (
            ctr
            .with_mounted_directory("/src", source)
            .with_workdir("/src")
            .with_exec(["uv", "pip", "install", "--system", "-r", "requirements.txt"])
        )

        # Load environment variables
        for key in ["OPENAI_API_KEY", "GITHUB_TOKEN"]:
            value = os.environ.get(key, "")
            pipeline = pipeline.with_env_variable(key, value)

        # Run and get output
        result = await pipeline.with_exec(["python", "main.py"]).stdout()
        print(result)

        # Export a result file to host
        await pipeline.file("/src/output.json").export("./output.json")


if __name__ == "__main__":
    asyncio.run(run_pipeline())
```

## Example 5: SSH Git Clone

```python
@object_type
class MyModule:
    @function
    async def clone_with_ssh(
        self,
        repository: str,
        ref: str,
        sock: dagger.Socket,
    ) -> dagger.Container:
        repo_dir = (
            dag.git(repository, ssh_auth_socket=sock)
            .ref(ref)
            .tree()
        )
        return (
            dag.container()
            .from_("alpine:latest")
            .with_directory("/src", repo_dir)
            .with_workdir("/src")
        )
```

## Example 6: CodingAgent with LLM

```python
@object_type
class CodingAgent:
    @function
    def go_program(self, assignment: str) -> dagger.Container:
        environment = (
            dag.env()
            .with_string_input("assignment", assignment, "the task")
            .with_container_input(
                "builder",
                dag.container().from_("golang").with_workdir("/app"),
                "Go build container",
            )
            .with_container_output("completed", "the built container")
        )
        work = (
            dag.llm()
            .with_env(environment)
            .with_prompt("""
                You are an expert Go programmer.
                Create files in $builder.
                Build the code to verify it compiles.
                Your assignment is: $assignment
            """)
        )
        return work.env().output("completed").as_container()
```

## Example 7: Node.js Build with Test and Publish

```python
@object_type
class HelloDagger:
    @function
    async def publish(
        self,
        source: Annotated[dagger.Directory, DefaultPath("/"), Doc("source dir")],
    ) -> str:
        await self.test(source)
        return await self.build(source).publish(
            f"ttl.sh/hello-dagger-{random.randrange(10**8)}"
        )

    @function
    def build(
        self,
        source: Annotated[dagger.Directory, DefaultPath("/"), Doc("source dir")],
    ) -> dagger.Container:
        build = (
            self.build_env(source)
            .with_exec(["npm", "run", "build"])
            .directory("./dist")
        )
        return (
            dag.container()
            .from_("nginx:1.25-alpine")
            .with_directory("/usr/share/nginx/html", build)
            .with_exposed_port(80)
        )

    @function
    async def test(
        self,
        source: Annotated[dagger.Directory, DefaultPath("/"), Doc("source dir")],
    ) -> str:
        return await (
            self.build_env(source)
            .with_exec(["npm", "run", "test:unit", "run"])
            .stdout()
        )

    @function
    def build_env(
        self,
        source: Annotated[dagger.Directory, DefaultPath("/"), Doc("source dir")],
    ) -> dagger.Container:
        node_cache = dag.cache_volume("node")
        return (
            dag.container()
            .from_("node:21-slim")
            .with_directory("/src", source)
            .with_mounted_cache("/root/.npm", node_cache)
            .with_workdir("/src")
            .with_exec(["npm", "install"])
        )
```
