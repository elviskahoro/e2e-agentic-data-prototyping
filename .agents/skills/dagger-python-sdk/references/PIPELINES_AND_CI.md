# Pipelines and CI Reference

## Standalone Pipeline Scripts

For scripts run via `dagger run python script.py` (not as Dagger modules):

### Connection Setup

```python
import sys
import asyncio
import dagger
from dagger import Container, dag

async def main():
    async with dagger.connection(
        config=dagger.Config(
            log_output=sys.stderr,
            timeout=3,
        ),
    ):
        container = dag.container().from_("python:3.11")
        output = await container.with_exec(["python", "-V"]).stdout()
        print(output)

if __name__ == "__main__":
    asyncio.run(main())
```

### Legacy Connection Pattern

```python
async with dagger.Connection(dagger.Config(log_output=sys.stderr)) as client:
    container = client.container().from_("python:3.11")
    output = await container.with_exec(["python", "-V"]).stdout()
```

## Pipeline Patterns

### Build and Test Pipeline

```python
async def pipeline():
    async with dagger.connection(config=dagger.Config(log_output=sys.stderr)):
        src = dag.host().directory(".", exclude=["node_modules/", ".dagger/"])

        # Build environment
        runner = (
            dag.container()
            .from_("node:16-slim")
            .with_directory("/src", src)
            .with_workdir("/src")
            .with_exec(["npm", "install"])
        )

        # Test
        test = runner.with_exec(["npm", "test", "--", "--watchAll=false"])

        # Build and export
        build_dir = test.with_exec(["npm", "run", "build"]).directory("./build")
        await build_dir.export("./build")
```

### Multi-Version Matrix Testing

```python
import anyio

async def pipeline():
    async with dagger.connection(config=dagger.Config(log_output=sys.stderr)):
        src = dag.host().directory(".")

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

### Docker Image Management Pipeline

```python
async def pipeline(
    use_local_docker_image: bool = False,
    export_docker_image: bool = False,
    image_address: str = "python:3.11-slim-buster",
    image_file_path: str = ".dagger/docker-images/python-3.11.tar",
):
    async with dagger.connection(config=dagger.Config(log_output=sys.stderr)):
        # Load or pull container
        if use_local_docker_image:
            ctr = dag.container().import_(
                source=dag.host().file(path=image_file_path, no_cache=True)
            )
        else:
            ctr = (
                dag.container()
                .from_(address=image_address)
                .with_exec(["pip", "install", "--upgrade", "pip"])
                .with_exec(["pip", "install", "uv"])
            )

        # Mount source and install deps
        ctr = (
            ctr
            .with_mounted_directory("/src", dag.host().directory("./src"))
            .with_workdir("/src")
            .with_exec(["uv", "pip", "install", "--system", "-r", "requirements.txt"])
        )

        # Optionally export for future local use
        if export_docker_image:
            await ctr.export(image_file_path)

        # Run the pipeline
        result = await ctr.with_exec(["python", "main.py"]).stdout()
        print(result)
```

### Environment Variable Loading

```python
import os

async def load_env_variables(ctr: Container) -> Container:
    env_vars = {
        "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY", ""),
        "GITHUB_TOKEN": os.environ.get("GITHUB_TOKEN", ""),
        "HYPERDX_API_KEY": os.environ.get("HYPERDX_API_KEY", ""),
    }
    for name, value in env_vars.items():
        ctr = ctr.with_env_variable(name=name, value=value)
    return ctr
```

## OpenTelemetry Tracing

Integrate tracing into pipeline scripts:

```python
from opentelemetry.trace import Status, StatusCode, get_current_span

# Decorator-based tracing
@tracer.start_as_current_span("operation_name")
async def my_operation():
    span = get_current_span()
    span.set_attribute(key="attribute_name", value="value")

    try:
        result = await do_work()
    except Exception as e:
        span.set_status(Status(StatusCode.ERROR, str(e)))
        raise

    return result
```

## GitHub Actions Integration

### Basic Dagger Module Call

```yaml
name: dagger
on:
  push:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Call Dagger Function
        uses: dagger/dagger-for-github@8.0.0
        with:
          version: "latest"
          verb: call
          module: .
          args: test
          cloud-token: ${{ secrets.DAGGER_CLOUD_TOKEN }}
```

### Dagger Python Pipeline in GitHub Actions

```yaml
jobs:
  pipeline:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Setup Dagger
        uses: dagger/dagger-for-github@8.0.0
        id: dagger-github

      - name: Install Dagger Python SDK
        run: pip install dagger-io

      - name: Run pipeline
        run: dagger run python my_pipeline.py
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

### Advanced: With Secrets, PR Creation, and Slack Notifications

```yaml
jobs:
  generate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Fetch Secrets (Doppler)
        uses: dopplerhq/secrets-fetch-action@v1.3.0
        with:
          doppler-token: ${{ secrets.DOPPLER_TOKEN }}
          inject-env-vars: true

      - uses: dagger/dagger-for-github@8.0.0
      - run: pip install dagger-io
      - run: dagger run python scripts/pipeline.py

      - name: Create Pull Request
        uses: peter-evans/create-pull-request@v7
        with:
          token: ${{ github.token }}
          commit-message: "automated: pipeline output"
          branch: gha/pipeline-output
          base: dev
          title: "Pipeline Output"
          delete-branch: true

      - name: Notify on failure
        if: failure()
        uses: slackapi/slack-github-action@v1.24.0
        with:
          method: chat.postMessage
          token: ${{ secrets.SLACK_BOT_TOKEN }}
          payload: |
            {"channel": "#alerts", "text": "Pipeline failed"}
```

### Dagger Shell in GitHub Actions

```yaml
- name: Dagger Shell command
  run: container | from alpine | file /etc/os-release | contents
  shell: dagger {0}
```

### Using Remote Dagger Modules

```yaml
- name: Test with remote module
  uses: dagger/dagger-for-github@8.0.0
  with:
    version: "latest"
    verb: call
    module: github.com/kpenfound/dagger-modules/golang@v0.2.0
    args: test --source=.

- name: Build and publish with remote module
  uses: dagger/dagger-for-github@8.0.0
  with:
    version: "latest"
    verb: call
    module: github.com/kpenfound/dagger-modules/golang@v0.2.0
    args: build-container --source=. publish --address=ttl.sh/my-app
```

### SSH Authentication in GitHub Actions

```yaml
- name: Set up SSH
  run: |
    eval "$(ssh-agent -s)"
    ssh-add - <<< '${{ secrets.SSH_PRIVATE_KEY }}'
```

## CLI Commands

| Command | Description |
|---------|-------------|
| `dagger call <function>` | Call a module function |
| `dagger call test` | Run the test function |
| `dagger call build` | Run the build function |
| `dagger call publish` | Run the publish function |
| `dagger run python script.py` | Run a Python pipeline script |
| `pip install dagger-io` | Install the Dagger Python SDK |
