# Modal Ecosystem Reference

Community projects, integration patterns, and real-world use cases from the Modal ecosystem.

## Use Case Categories

### AI/ML Inference (Largest Category)

**Image Generation:**
- ComfyUI workflows as API — `modal-comfy-worker`, `comfyui-deploy`
- Stable Diffusion / Flux serving — `modal-photobooth`, `diffusion-canvas`
- LoRA training in UI — `modal-deploy-kohya-ss`
- Face swapping — `facestream` (Deep Live Cam on Modal)

**LLM Serving:**
- vLLM-based OpenAI-compatible API — `llm-hosting`, `openrouter-runner`
- llama.cpp chatbots — `ChitChat`, `fastllm`
- Multi-LLM comparison — `llm-comparison-backend`

**Embeddings & RAG:**
- Embedding servers — `infinity`
- Vision-language RAG — `vision-is-all-you-need` (ColPALI + GPT-4o)
- Relationship advice RAG — `Agony_Aunt_RAG`

### AI/ML Training

- LLM fine-tuning — `axolotl` (uses Modal for CI)
- Knowledge distillation — `distillKitPlus`
- Sparse autoencoders on LLM activations — `latent-sae`
- RLHF — `verl` (via multinode-training-guide)
- LoRA training — Kohya framework on Modal

### Scientific Computing

- Protein folding — `foldism`, `helix`, `biomodals`
- Variant effect prediction — `variant-analysis-evo2`
- Computational chemistry — GMTKN55 benchmark
- Mutation detection — `breseq-on-modal`
- Weather modeling — `skyrim`, `ai-models-for-all`

### Web Applications

- Full-stack Python apps with FastHTML
- SvelteKit + Modal Python endpoints — `sveltekit-modal`
- Real-time collaborative editing — `modal-crdts`
- Data dashboards — Metabase on Modal
- Live transcription — `cbp-translate`

### Developer Tools

- GPU profiling — `ncompass`
- Personal devboxes — `DevBox`
- Distributed joblib — `joblib-modal`
- MCP integration — `modal-mcp-toolbox`
- GitHub Copilot extension — `modal-docs-copilot-extension`

## Integration Patterns

### Framework Integrations

| Framework | Integration | Pattern |
|-----------|-------------|---------|
| FastAPI | Native | `@modal.fastapi_endpoint()` or `@modal.asgi_app()` |
| Flask | Native | `@modal.wsgi_app()` |
| FastHTML | Community | Full-stack Python web apps |
| SvelteKit | Community | Python backend endpoints |
| Gradio | Common | Mount as ASGI app |
| Streamlit | Common | Via `@modal.web_server()` |

### ML Framework Integrations

| Framework | Pattern |
|-----------|---------|
| PyTorch | Direct — GPU functions + DDP |
| Transformers (HF) | Model loading in `@modal.enter()` |
| vLLM | Dedicated serving pattern |
| Ray | Multi-node cluster class |
| PyTorch Lightning | Fabric launcher |
| Megatron-LM | Torchrun + Megatron CLI |
| ComfyUI | Workflow API server |
| Axolotl | CI/fine-tuning pipeline |

### Data & MLOps Integrations

| Tool | Pattern |
|------|---------|
| Weights & Biases | Secret + `wandb.init()` in training |
| ZenML | Pipeline orchestration on Modal |
| DuckDB | Query Parquet on CloudBucketMount |
| Jupyter | `modal.forward()` for notebook server |
| S3/GCS | `modal.CloudBucketMount` |

### External Service Integrations

| Service | Pattern |
|---------|---------|
| Hugging Face Hub | Secret + Volume cache |
| OpenAI API | Secret + `@app.function()` |
| Anthropic API | Secret + sandbox for tool use |
| Stripe | Payment webhooks via endpoints |
| Discord | Bot running as scheduled function |
| Telegram | Bot with Stripe payments |
| MongoDB | Database client in function |

## Architecture Patterns from Community

### API-as-a-Service (Most Common)
```
User Request → Modal Web Endpoint → GPU Function → Response
```
Used by: llm-hosting, infinity, marker, etc.

### Webhook/Event-Driven
```
External Event → Modal Endpoint → Spawn Processing → Store Result
```
Used by: bots, payment webhooks, CI triggers

### Pipeline Processing
```
Input → Stage 1 (CPU) → Stage 2 (GPU) → Stage 3 (Storage)
```
Used by: document processing, dataset ingestion, training pipelines

### Real-Time Streaming
```
Client ← SSE/WebSocket ← Modal Function (GPU) ← Model
```
Used by: diffusion-canvas, chat interfaces, live transcription

### Scheduled Processing
```
Cron Trigger → Modal Function → External API → Store/Notify
```
Used by: data collection bots, monitoring, report generation

## Notable Design Decisions in Community

1. **Separate model download from serving** — Download in image build or Volume, serve from cache
2. **Use classes for stateful services** — `@app.cls()` with `@modal.enter()` for model loading
3. **Volume per concern** — Separate volumes for models, data, checkpoints
4. **Fallback GPU lists** — `["h100", "a100", "any"]` for availability
5. **Memory snapshots for production** — `enable_memory_snapshot=True` for fast cold starts
6. **Pin everything** — Versions, image tags, model revisions
