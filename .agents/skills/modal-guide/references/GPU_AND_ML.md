# GPU and ML Reference

Comprehensive reference for GPU configuration, model serving, and ML patterns on Modal.

## GPU Types and Selection

### Available GPUs

| GPU | VRAM | Best For |
|-----|------|----------|
| `T4` | 16 GB | Light inference, testing |
| `L4` | 24 GB | Inference, small models |
| `A10G` | 24 GB | Inference, medium models |
| `A100` | 80 GB | Training, large inference |
| `H100` | 80 GB | Training, high-throughput inference |
| `H200` | 141 GB | Large model training |
| `B200` | 192 GB | Largest models |

### Specifying GPUs

```python
# Single GPU
@app.function(gpu="H100")

# Multi-GPU (per container)
@app.function(gpu="H100:8")  # 8 H100s

# Fallback list — tries in order
@app.function(gpu=["h100", "a100", "any"])

# "any" matches L4, A10G, or T4
@app.function(gpu="any")
```

## Model Serving Pattern (Standard)

```python
model_volume = modal.Volume.from_name("model-cache", create_if_missing=True)

@app.cls(
    gpu="H100",
    image=image,
    volumes={"/root/.cache/huggingface": model_volume},
    enable_memory_snapshot=True,  # Snapshot after enter() for fast cold starts
)
class ModelServer:
    @modal.enter()
    def load(self):
        """Runs once per container. Load model weights here."""
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.tokenizer = AutoTokenizer.from_pretrained("meta-llama/Llama-3-8B")
        self.model = AutoModelForCausalLM.from_pretrained(
            "meta-llama/Llama-3-8B",
            torch_dtype=torch.bfloat16,
            device_map="auto",
        )

    @modal.method()
    def generate(self, prompt: str) -> str:
        inputs = self.tokenizer(prompt, return_tensors="pt").to("cuda")
        outputs = self.model.generate(**inputs, max_new_tokens=256)
        return self.tokenizer.decode(outputs[0])
```

## vLLM Serving Pattern

```python
MODELS_DIR = "/llms"
MODEL_NAME = "Qwen/Qwen3-4B"
MODEL_REVISION = "a]specific-commit-hash"

vllm_image = (
    modal.Image.from_registry("nvidia/cuda:12.8.0-devel-ubuntu24.04", add_python="3.12")
    .entrypoint([])
    .pip_install("vllm==0.7.3", "huggingface_hub[hf_transfer]~=0.27.0")
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1"})
    .run_function(download_model, secrets=[hf_secret])
)

@app.cls(
    gpu="H100",
    image=vllm_image,
    volumes={MODELS_DIR: model_volume, "/root/.cache/vllm": vllm_cache_volume},
)
class Inference:
    @modal.enter()
    def init_engine(self):
        from vllm.engine.arg_utils import AsyncEngineArgs
        from vllm.engine.async_llm_engine import AsyncLLMEngine

        self.engine = AsyncLLMEngine.from_engine_args(
            AsyncEngineArgs(
                model=MODELS_DIR + "/" + MODEL_NAME,
                gpu_memory_utilization=0.95,
                max_model_len=8192,
                enforce_eager=False,  # Use CUDA graphs
            )
        )

    @modal.method()
    async def generate(self, prompt: str):
        from vllm import SamplingParams
        params = SamplingParams(max_tokens=512, temperature=0.7)
        # ... streaming generation
```

## GPU Packing Pattern (Multiple Models per GPU)

```python
@app.cls(gpu="A10G", max_containers=1)
@modal.concurrent(max_inputs=100)
class PackedServer:
    n_models = 4  # Pack N model copies on one GPU

    @modal.enter()
    async def load_models(self):
        self.model_pool = asyncio.Queue()
        for i in range(self.n_models):
            model = SentenceTransformer("model-name", device="cuda")
            await self.model_pool.put(model)

    @modal.method()
    async def predict(self, text: str):
        model = await self.model_pool.get()
        try:
            return model.encode(text).tolist()
        finally:
            await self.model_pool.put(model)
```

## Resumable Training Pattern

```python
checkpoint_vol = modal.Volume.from_name("checkpoints", create_if_missing=True)

@app.function(
    gpu="H100",
    volumes={"/checkpoints": checkpoint_vol},
    timeout=86400,  # 24 hours max
    retries=modal.Retries(max_retries=10, initial_delay=0.0),
)
def train():
    # Resume from checkpoint if preempted
    ckpt_path = "/checkpoints/latest.pt"
    if os.path.exists(ckpt_path):
        checkpoint = torch.load(ckpt_path)
        model.load_state_dict(checkpoint["model"])
        optimizer.load_state_dict(checkpoint["optimizer"])
        start_step = checkpoint["step"]
    else:
        start_step = 0

    for step in range(start_step, total_steps):
        # ... training loop ...

        if step % save_interval == 0:
            torch.save({
                "model": model.state_dict(),
                "optimizer": optimizer.state_dict(),
                "step": step,
            }, ckpt_path)
            checkpoint_vol.commit()
```

## Cold Start Optimization

| Strategy | How | Impact |
|----------|-----|--------|
| Memory snapshots | `enable_memory_snapshot=True` on `@app.cls()` | Fastest restart — skips `@modal.enter()` on warm |
| Volume caching | Cache model weights in `modal.Volume` | Avoid re-downloading models |
| Smaller images | Minimize apt/pip installs | Faster image pull |
| Keep-warm | `keep_warm=1` on `@app.function()` | Always 1 container ready (costs money) |
| GPU fallback | `gpu=["h100", "a100", "any"]` | Don't wait for specific GPU |

## Monitoring with Weights & Biases

```python
@app.function(
    gpu="H100",
    secrets=[modal.Secret.from_name("wandb-secret")],
)
def train():
    import wandb
    wandb.init(project="my-project")
    # ... training with wandb.log()
```
