# TimesFM Real Model Installation Runbook

## Current Status

Your system has TWO Python environments:
- **System Python** (3.14): Runs the trading OS supervisor
- **TimesFM venv** (3.11): Isolated environment for the model

The production adapter (`timesfm_adapter_production.py`) bridges both:
- Detects if real TimesFM is installed
- Uses it if available
- Falls back to statistical forecaster if not — **no crash, no downtime**

---

## Install Real TimesFM (When Network is Stable)

The recommended model is `google/timesfm-2.5-200m-pytorch` — it works on CPU and doesn't require JAX.

### Step 1: Complete PyTorch Install

A background process was started. Check status first:

```bash
ps aux | grep "uv pip" | grep -v grep
```

If still running, wait. If done or failed, run:

```bash
cd /mnt/e/NomadCrew[GROWTH]/trading-os

# Method A: uv (fastest)
/home/naqeeb/.local/bin/uv pip install --python timesfm_env/bin/python \
    torch transformers huggingface_hub safetensors \
    --index-url https://download.pytorch.org/whl/cpu

# Method B: pip direct (if uv fails)
./timesfm_env/bin/pip install \
    torch transformers huggingface_hub safetensors \
    --index-url https://download.pytorch.org/whl/cpu
```

This downloads ~200MB. If your connection is flaky, use:
```bash
# Resume interrupted downloads
pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
```

### Step 2: Verify Install

```bash
./timesfm_env/bin/python -c "import torch; print('torch', torch.__version__)"
./timesfm_env/bin/python -c "import transformers; print('transformers', transformers.__version__)"
./timesfm_env/bin/python -c "import huggingface_hub; print('hf_hub OK')"
```

All three must succeed.

### Step 3: Test Model Download

```bash
./timesfm_env/bin/python -c "
from transformers import AutoModel
model = AutoModel.from_pretrained('google/timesfm-2.5-200m-pytorch', trust_remote_code=True)
print('Model loaded!' , model.config.hidden_size)
"
```

First run downloads ~400MB model weights from HuggingFace. Subsequent runs use cache.

---

## Optional: Use Google timesfm Package

If you prefer Google's official package over HuggingFace:

```bash
# Requires Python 3.10 (paxml/praxis don't support 3.11+)
python3.10 -m venv timesfm_jax_env
./timesfm_jax_env/bin/pip install timesfm==1.3.0
```

Then update `config/settings.yaml`:
```yaml
intelligence:
  timesfm_model_id: "google/timesfm-1.0-200m"
```

---

## Environment Variable

To point the adapter at a custom venv:
```bash
export AUTONOME_TIMESFM_VENV=/path/to/your/venv/lib/python3.11/site-packages
```

---

## One-Command Status Check

```bash
cd /mnt/e/NomadCrew[GROWTH]/trading-os/v2
python3 -c "
from autonome.intelligence.timesfm_adapter_production import TimesFMAdapter
a = TimesFMAdapter('google/timesfm-2.5-200m-pytorch')
print('Backend:', a._backend_name)
print('Is real:', a.is_real)
if a.is_real:
    print('TIMESFM IS LIVE')
else:
    print('Statistical fallback active — install real model (see TIMESFM_INSTALL.md)')
"
```

Expected output:
- Before install: `statistical` / `Is real: False`
- After install: `timesfm_real` / `Is real: True`

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `torch` import fails with symbol error | Uninstall conflicting torch: `pip uninstall torch` then reinstall with `--index-url` |
| `401` from HuggingFace | Model is public, 401 means network/DNS issue. Retry with `HF_HUB_OFFLINE=0` |
| Download hangs | Use `--timeout 300` or switch to a mirror CDN |
| Out of memory | TimesFM 200M fits in 2GB RAM. If still OOM, reduce `torch_dtype` to `float16` |
| `No module named transformers` | Install with: `pip install transformers huggingface_hub safetensors` |
