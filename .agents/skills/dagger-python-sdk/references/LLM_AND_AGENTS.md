# LLM and Agents Reference

Dagger supports LLM-powered environments for AI-driven workflows including code generation, automated fixing, and multi-agent systems.

## Core Concepts

- **Environment (`dag.env()`)** — defines inputs and outputs for an LLM task
- **Workspace (`dag.workspace()`)** — a code editing environment with read/write/test tools
- **LLM (`dag.llm()`)** — executes a prompt within an environment

## Environment Setup

### Creating an Environment

```python
environment = (
    dag.env(privileged=True)
    .with_string_input("assignment", value, "the task to complete")
    .with_container_input("builder", container, "container to use for building")
    .with_workspace_input("before", dag.workspace(source=source), "code and tests")
    .with_file_input("data", file, "input data file")
    .with_container_output("completed", "the built container")
    .with_workspace_output("after", "modified code")
    .with_string_output("summary", "description of changes")
    .with_directory_output("result", "output directory")
)
```

### Input Types

| Method | Input Type | Description |
|--------|-----------|-------------|
| `.with_string_input(name, value, desc)` | `str` | Plain text input |
| `.with_container_input(name, ctr, desc)` | `Container` | Container with tools |
| `.with_workspace_input(name, ws, desc)` | `Workspace` | Code editing workspace |
| `.with_file_input(name, file, desc)` | `File` | File input |

### Output Types

| Method | Output Type | Description |
|--------|-----------|-------------|
| `.with_string_output(name, desc)` | `str` | Text output |
| `.with_container_output(name, desc)` | `Container` | Modified container |
| `.with_workspace_output(name, desc)` | `Workspace` | Modified workspace |
| `.with_directory_output(name, desc)` | `Directory` | Output directory |

## Running an LLM

### Basic Usage

```python
work = (
    dag.llm()
    .with_env(environment)
    .with_prompt("Your detailed prompt here")
)
```

### With a Model

```python
work = dag.llm(model="gpt-4o").with_env(environment).with_prompt("...")
```

### With a Prompt File

```python
prompt_file = dag.current_module().source().file("prompt.txt")
work = dag.llm().with_env(environment).with_prompt_file(prompt_file)
```

### Getting Results

```python
# String output
summary = await work.env().output("summary").as_string()

# Container output
container = work.env().output("completed").as_container()

# Workspace output
workspace = work.env().output("after").as_workspace()
source_dir = workspace.source()
ws_container = workspace.container()

# Directory output
directory = work.env().output("result").as_directory()

# Last reply (raw LLM response)
reply = await work.last_reply()
```

## Workspaces

Workspaces provide file read/write and test tools for LLM agents.

### Creating a Workspace

```python
workspace = dag.workspace(source=source_directory)
```

### Workspace Methods

| Method | Description |
|--------|-------------|
| `.source()` | Get the source directory |
| `.container()` | Get the underlying container |
| `.read_file(path)` | Read a file (async) |
| `.write_file(path, contents)` | Write a file (returns self) |
| `.list_files(path)` | List files (async) |

### Custom Workspace Object

```python
from typing import Annotated, Self
from dagger import Container, dag, Directory, Doc, function, object_type, ReturnType

@object_type
class Workspace:
    ctr: Container
    source: Directory

    @classmethod
    async def create(
        cls,
        source: Annotated[Directory, Doc("The context"), DefaultPath("/")],
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
    async def read_file(self, path: Annotated[str, Doc("File path")]) -> str:
        return await self.ctr.file(path).contents()

    @function
    def write_file(
        self,
        path: Annotated[str, Doc("File path")],
        contents: Annotated[str, Doc("Contents")],
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
        cmd = (
            self.ctr
            .with_exec(["pytest", "--tb=short"], expect=ReturnType.ANY)
        )
        if await cmd.exit_code() != 0:
            raise Exception(f"Tests failed: {await cmd.stderr()}")
        return await cmd.stdout()

    @function
    def container(self) -> Container:
        return self.ctr
```

## AI-Powered Code Fixing

### Local Fix (Returns Directory)

```python
@function
def fix(self, source: Annotated[dagger.Directory, DefaultPath("/")]) -> dagger.Directory:
    environment = (
        dag.env(privileged=True)
        .with_workspace_input("before", dag.workspace(source=source), "code and tests")
        .with_workspace_output("after", "modified code")
        .with_string_output("summary", "changes made")
    )
    work = dag.llm().with_env(environment).with_prompt("""
        - You are an expert Python developer
        - The tests are failing
        - Fix the issues so tests pass
        - Always write changes to the workspace
        - Run the test tool after writing changes
        - You are not done until tests pass
    """)
    return work.env().output("after").as_directory()
```

### GitHub Fix (Opens PR)

```python
async def fix_github(
    self,
    source: Annotated[dagger.Directory, DefaultPath("/")],
    repository: Annotated[str, Doc("Owner and repository name")],
    ref: Annotated[str, Doc("Git ref")],
    token: Annotated[Secret, Doc("GitHub API token")],
) -> str:
    environment = (
        dag.env(privileged=True)
        .with_workspace_input("before", dag.workspace(source=source), "code and tests")
        .with_workspace_output("after", "modified code")
        .with_string_output("summary", "changes made")
    )
    work = dag.llm().with_env(environment).with_prompt_file(prompt_file)

    summary = await work.env().output("summary").as_string()
    diff_file = await (
        work.env().output("after").as_workspace().container()
        .with_exec(["sh", "-c", "git diff > /tmp/a.diff"])
        .file("/tmp/a.diff")
    )

    pr_url = await dag.github_api().create_pr(repository, ref, diff_file, token)
    return f"PR created: {pr_url}"
```

## Multi-Agent System

Chain LLM agents together by passing one agent's output as another's input:

```python
@function
async def demo(self, chat_model: str = "gpt-4o", coder_model: str = "gpt-o1") -> str:
    # Agent 1: Code generator
    coder_env = (
        dag.env()
        .with_toy_workspace_input("workspace", ws, "tools to build code")
        .with_string_input("assignment", task, "task to complete")
        .with_toy_workspace_output("workspace", "completed assignment")
    )
    coder = dag.llm(model=coder_model).with_env(coder_env).with_prompt_file(prompt)

    # Run the code from agent 1
    output_file = (
        coder.env().output("workspace").as_toy_workspace().container()
        .with_exec(["go", "run", "."], redirect_stdout="output.txt")
        .file("output.txt")
    )

    # Agent 2: Summarizer (uses output from agent 1)
    summary_env = dag.env().with_file_input("data", output_file, "the data to summarize")
    summarizer = (
        dag.llm(model=chat_model)
        .with_env(summary_env)
        .with_prompt("Summarize the data in $data")
    )

    return await summarizer.last_reply()
```

## Prompt Variable References

In prompts, reference environment inputs by name with `$`:

```python
.with_prompt("""
    Your assignment is: $assignment
    Use the tools in $builder to create the code
    Write output to $workspace
""")
```
