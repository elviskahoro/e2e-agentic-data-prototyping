# Multi-Node Training Reference

Complete reference for distributed multi-node GPU training on Modal.

## Core Pattern

```python
import modal

app = modal.App("distributed-training")

data_vol = modal.Volume.from_name("training-data", create_if_missing=True)
ckpt_vol = modal.Volume.from_name("checkpoints", create_if_missing=True)

N_NODES = 2
GPUS_PER_NODE = 8

@app.function(
    gpu=f"H100:{GPUS_PER_NODE}",
    volumes={"/data": data_vol, "/checkpoints": ckpt_vol},
    timeout=86400,
    retries=modal.Retries(max_retries=2, initial_delay=0.0),
)
@modal.experimental.clustered(N_NODES, rdma=True)
def train():
    cluster_info = modal.experimental.get_cluster_info()

    from torch.distributed.run import parse_args, run
    run(parse_args([
        f"--nnodes={N_NODES}",
        f"--nproc-per-node={GPUS_PER_NODE}",
        f"--node_rank={cluster_info.rank}",
        f"--master_addr={cluster_info.container_ips[0]}",
        "--master_port=29500",
        "train_script.py",
    ]))
```

## Cluster Info API

```python
cluster_info = modal.experimental.get_cluster_info()

cluster_info.rank              # int: 0-indexed container rank
cluster_info.container_ips     # list[str]: IPv6 addresses (control plane)
cluster_info.container_ipv4_ips  # list[str]: IPv4 addresses (RDMA data plane)
```

- `container_ips[0]` is always the master node
- Use IPv4 IPs for RDMA/InfiniBand communication

## Network Configuration

### RDMA / High-Speed Networking

```python
# H100 clusters (AWS EFA)
@app.function(
    gpu="H100:8",
    experimental_options={"efa_enabled": True},
)
@modal.experimental.clustered(n_nodes, rdma=True)

# H200/B200 clusters (InfiniBand — default with rdma=True)
@app.function(gpu="H200:8")
@modal.experimental.clustered(n_nodes, rdma=True)
```

### NCCL Environment Variables

```python
# Common NCCL tuning
os.environ["NCCL_IB_GID_INDEX"] = "3"
os.environ["NCCL_NET"] = "IB"
os.environ["NCCL_NVLS_ENABLE"] = "1"
os.environ["CUDA_DEVICE_MAX_CONNECTIONS"] = "1"
```

## Framework Launchers

### Torchrun (PyTorch native)

```python
from torch.distributed.run import parse_args, run

run(parse_args([
    f"--nnodes={N_NODES}",
    f"--nproc-per-node={GPUS_PER_NODE}",
    f"--node_rank={cluster_info.rank}",
    f"--master_addr={cluster_info.container_ips[0]}",
    "--master_port=29500",
    "train.py",
    "--batch_size=32",
]))
```

### Hugging Face Accelerate

```python
import subprocess

subprocess.run([
    "accelerate", "launch",
    "--num_processes", str(GPUS_PER_NODE),
    "--num_machines", str(N_NODES),
    "--machine_rank", str(cluster_info.rank),
    "--main_process_ip", cluster_info.container_ips[0],
    "--main_process_port", "29500",
    "--mixed_precision", "bf16",
    "train.py",
], check=True)
```

### PyTorch Lightning Fabric

```python
subprocess.run([
    "fabric", "run",
    "--accelerator=gpu",
    "--strategy=ddp",
    f"--devices={GPUS_PER_NODE}",
    f"--num-nodes={N_NODES}",
    f"--node-rank={cluster_info.rank}",
    f"--main-address={cluster_info.container_ips[0]}",
    "train.py",
], check=True)
```

### Megatron-LM (Large LLM Training)

```python
subprocess.run([
    "torchrun",
    f"--nnodes={N_NODES}",
    f"--node_rank={cluster_info.rank}",
    f"--master_addr={cluster_info.container_ips[0]}",
    "--master_port=29500",
    "--nproc_per_node=8",
    "train.py",
    "--tensor_model_parallel_size=2",
    "--pipeline_model_parallel_size=4",
    "--expert_model_parallel_size=4",
], check=True)
```

### Ray Cluster

```python
@app.cls(gpu="H100:8", timeout=86400)
@modal.experimental.clustered(N_NODES, rdma=True)
class RayCluster:
    @modal.enter()
    def start_ray(self):
        cluster_info = modal.experimental.get_cluster_info()
        self.rank = cluster_info.rank

        if self.rank == 0:
            subprocess.Popen([
                "ray", "start", "--head",
                f"--node-ip-address={cluster_info.container_ipv4_ips[0]}",
                "--dashboard-host=0.0.0.0",
            ])
            import ray
            ray.init(address="auto")
            # Wait for all workers
            for _ in range(60):
                alive = [n for n in ray.nodes() if n["Alive"]]
                if len(alive) == N_NODES:
                    break
                time.sleep(1)
        else:
            subprocess.Popen([
                "ray", "start",
                f"--node-ip-address={cluster_info.container_ipv4_ips[self.rank]}",
                "--address", f"{cluster_info.container_ipv4_ips[0]}:6379",
            ])

    @modal.method()
    async def submit_job(self, cmd: str):
        from ray.job_submission import JobSubmissionClient
        client = JobSubmissionClient("http://127.0.0.1:8265")
        job_id = client.submit_job(entrypoint=cmd)
        # ... poll or tail logs
```

## Training Script Patterns

### DDP Initialization (Inside train.py)

```python
import os, torch
from torch.distributed import init_process_group

ddp = int(os.environ.get("RANK", -1)) != -1
if ddp:
    init_process_group(backend="nccl")
    rank = int(os.environ["RANK"])
    local_rank = int(os.environ["LOCAL_RANK"])
    world_size = int(os.environ["WORLD_SIZE"])
    device = f"cuda:{local_rank}"
    torch.cuda.set_device(device)
    master_process = (rank == 0)
else:
    master_process = True
    device = "cuda"
```

### Gradient Accumulation with DDP

```python
total_batch_size = 524288  # tokens
batch_per_rank = total_batch_size // world_size
grad_accum_steps = batch_per_rank // (micro_batch_size * seq_len)
```

### Rank-Zero-Only Operations

```python
# Only rank 0 downloads data
if rank == 0:
    download_dataset("/data")
    data_vol.commit()
torch.distributed.barrier()  # Sync all ranks

# Only rank 0 logs metrics
if master_process:
    wandb.log({"loss": loss, "step": step})

# Only rank 0 saves checkpoints
if master_process and step % save_every == 0:
    torch.save(state, "/checkpoints/latest.pt")
    ckpt_vol.commit()
```

## Container Image for Training

```python
image = (
    modal.Image.from_registry(
        "nvidia/cuda:12.4.0-devel-ubuntu22.04",
        add_python="3.10",
    )
    .entrypoint([])
    .apt_install(
        "libibverbs-dev",      # InfiniBand/RDMA
        "libibverbs1",
        "libhwloc15",         # NCCL topology
        "libnl-route-3-200",  # Network config
    )
    .pip_install(
        "torch==2.6.0",
        "transformers==4.51.3",
    )
    .add_local_dir("./src", remote_path="/root/src")
)
```

## Health Checks

Available diagnostic scripts from the multinode-training-guide:

```bash
# EFA tests
modal run health-checks/modal_pingpong_efa.py
modal run health-checks/modal_bw_efa.py

# InfiniBand tests
modal run health-checks/modal_pingpong_ib.py
modal run health-checks/modal_bw_ib.py
```

## Framework Comparison

| Framework | Best For | Launcher | Complexity |
|-----------|----------|----------|------------|
| Torchrun | General PyTorch DDP | `torch.distributed.run` | Low |
| Accelerate | HuggingFace ecosystem | `accelerate launch` | Low |
| Lightning Fabric | PyTorch Lightning | `fabric run` | Low |
| Megatron-LM | 70B+ LLMs | torchrun + megatron CLI | High |
| Ray | RL, heterogeneous workloads | Ray cluster + job submission | Medium |

## File Structure Convention

```
my-training/
├── modal_train.py      # Modal entrypoint (cluster setup, volumes, etc.)
├── train.py            # Actual training script (framework-specific)
├── config.yaml         # Training hyperparameters
└── utils/              # Helper scripts
```

## Source Examples

For complete working examples, refer to:
- `modal-labs/multinode-training-guide/nanoGPT/` — Simple torchrun DDP
- `modal-labs/multinode-training-guide/resnet50/` — ResNet benchmark
- `modal-labs/multinode-training-guide/starcoder/` — HF Accelerate
- `modal-labs/multinode-training-guide/lightning/` — PyTorch Lightning
- `modal-labs/multinode-training-guide/megatron/` — Megatron-LM for large models
- `modal-labs/multinode-training-guide/ray/` — Ray cluster pattern
- `modal-labs/multinode-training-guide/verl/` — RLHF with veRL on Ray
- `modal-labs/modal-examples/14_clusters/` — Basic clustered example
